"""Render the unchanged input PDF with published normalized block positions."""
import argparse
import json
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw
import pypdfium2 as pdfium


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('--pdf', type=Path, required=True)
    cli.add_argument('--normalized', type=Path, required=True)
    cli.add_argument('--output-dir', type=Path, required=True)
    args = cli.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    normalized = json.loads(args.normalized.read_text())
    subprocess.run(['pdftoppm', '-r', '130', '-png', str(args.pdf), str(args.output_dir/'page')], check=True, capture_output=True)
    geometry = {}
    with pdfium.PdfDocument(args.pdf) as pdf:
        for i in range(len(pdf)):
            page = pdf[i]; geometry[i+1] = page.get_size(); page.close()
    dimensions_valid = len(normalized['pages']) == len(geometry) and all(abs(p['width']-geometry[p['page_no']][0]) < .01 and abs(p['height']-geometry[p['page_no']][1]) < .01 for p in normalized['pages'])
    index = []
    for page in normalized['pages']:
        number = page['page_no']
        image = Image.open(args.output_dir/f'page-{number}.png').convert('RGB')
        draw = ImageDraw.Draw(image)
        for block in normalized['blocks']:
            for location in block['locations']:
                if location['page_no'] != number: continue
                box = [location['bbox'][0]*image.width, location['bbox'][1]*image.height, location['bbox'][2]*image.width, location['bbox'][3]*image.height]
                color = '#176bb0' if block['kind'] == 'table_cell' else '#c14332'
                draw.rectangle(box, outline=color, width=2)
                label = block['block_id'].rsplit(':',1)[-1].lstrip('0')
                draw.text((max(0,box[0]-17), box[1]), label, fill=color)
                index.append({'page_no':number, 'block_id':block['block_id'], 'text':block['text'], 'bbox':location['bbox'], 'charspan':location['charspan']})
        image.save(args.output_dir/f'positions-{number}.png')
    result = {'dpi':130, 'page_count':len(geometry), 'boxes':len(index), 'pdf_geometry_matches_normalized':dimensions_valid, 'legend':'Red paragraphs, blue table cells; labels are block sequence numbers. Original page renders retained.', 'locations':index}
    (args.output_dir/'index.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ['page_count','boxes','pdf_geometry_matches_normalized']}))
    raise SystemExit(0 if dimensions_valid else 1)


if __name__ == '__main__':
    main()
