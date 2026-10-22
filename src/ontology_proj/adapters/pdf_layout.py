"""Local PDF geometry supplement; no OCR, text rewriting or network."""
import math
from pathlib import Path

REVISION = 'pdf-layout-1'


def read_pdf_layout(path: Path) -> dict:
    import pypdfium2 as pdfium
    from pypdf import PdfReader

    result = {'revision': REVISION, 'pages': [], 'images': [], 'lines': [], 'warnings': []}
    reader = PdfReader(path)
    with pdfium.PdfDocument(str(path)) as pdf:
        for index, source in enumerate(reader.pages):
            page_no = index + 1
            width, height = float(source.mediabox.width), float(source.mediabox.height)
            result['pages'].append({'page_no': page_no, 'width': width, 'height': height,
                                    'rotation': int(source.get('/Rotate', 0)) % 360})
            if (source.get('/Rotate', 0) or float(source.mediabox.left) or float(source.mediabox.bottom)
                    or list(source.cropbox) != list(source.mediabox)):
                result['warnings'].append({'code': 'PDF_GEOMETRY_UNSUPPORTED', 'page_no': page_no})
                continue
            page = pdf[index]
            try:
                for number, obj in enumerate(page.get_objects()):
                    if obj.type != pdfium.raw.FPDF_PAGEOBJ_IMAGE:
                        continue
                    if obj.level:
                        # Child bounds omit enclosing Form transforms; do not invent page geometry.
                        result['warnings'].append({'code': 'PDF_NESTED_IMAGE_UNSUPPORTED', 'page_no': page_no})
                        continue
                    left, bottom, right, top = obj.get_bounds()
                    box = [left / width, (height - top) / height, right / width, (height - bottom) / height]
                    if not all(math.isfinite(v) and 0 <= v <= 1 for v in box) or box[0] >= box[2] or box[1] >= box[3]:
                        result['warnings'].append({'code': 'PDF_IMAGE_LOCATION_UNAVAILABLE', 'page_no': page_no})
                        continue
                    result['images'].append({'source_ref': f'pdf:page:{page_no}:object:{number}',
                        'page_no': page_no, 'bbox': box, 'pixel_size': list(obj.get_px_size()),
                        'raw_bbox': [left, bottom, right, top], 'location_level': 'region'})
            finally:
                page.close()

            # Only actually stroked axis-aligned segments count as ruling evidence.
            pending, position, origin = [], None, None
            def point(x, y, matrix):
                a, b, c, d, e, f = matrix
                return ((a*x+c*y+e)/width, (height-(b*x+d*y+f))/height)
            def visitor(op, args, cm, tm):
                nonlocal position, origin
                if op == b'm':
                    position = origin = point(float(args[0]), float(args[1]), cm)
                elif op == b'l':
                    target = point(float(args[0]), float(args[1]), cm)
                    if position is not None: pending.append([*position, *target])
                    position = target
                elif op == b're':
                    x, y, w, h = map(float, args)
                    pts = [point(x,y,cm), point(x+w,y,cm), point(x+w,y+h,cm), point(x,y+h,cm)]
                    pending.extend([*pts[i], *pts[(i+1)%4]] for i in range(4))
                elif op in (b'h', b's', b'b', b'b*') and position is not None and origin is not None:
                    pending.append([*position, *origin]); position = origin
                if op in (b'S', b's', b'B', b'B*', b'b', b'b*'):
                    for line in pending:
                        x0,y0,x1,y1 = line
                        if (all(math.isfinite(v) and 0 <= v <= 1 for v in line)
                                and (abs(x0-x1)<1e-5 or abs(y0-y1)<1e-5)
                                and max(abs(x0-x1), abs(y0-y1)) > .01):
                            result['lines'].append({'page_no': page_no, 'points': line})
                if op in (b'S', b's', b'B', b'B*', b'b', b'b*', b'f', b'F', b'f*', b'n'):
                    pending.clear(); position = origin = None
            source.extract_text(visitor_operand_before=visitor)
    return result
