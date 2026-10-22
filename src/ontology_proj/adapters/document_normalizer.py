"""Pure structural adaptation of saved Docling JSON; no I/O or business inference."""
import copy
import json
import math
import re

from ontology_proj.application.dto import ImportContext


class NormalizationError(Exception):
    code = "INVALID_NATIVE_DOCUMENT"

    def __init__(self):
        super().__init__("Native document structure or normalization context is invalid")


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


class DoclingNormalizer:
    def convert(self, native_document: dict, document_id: str, context: ImportContext,
                page_rotations: dict[int, int], *, pdf_layout: dict | None = None, ocr: dict | None = None) -> dict:
        try:
            result = self._convert(native_document, document_id, context, page_rotations)
            if ocr is not None:
                json.dumps(ocr, allow_nan=False)
                result['ocr'] = copy.deepcopy(ocr)
            if native_document.get('pictures') or pdf_layout is not None:
                from ontology_proj.adapters.document_structure import enrich_structure
                result['structure'] = enrich_structure(result, native_document, pdf_layout)
                result['warnings'].extend({'source_ref': 'structure', **copy.deepcopy(d)}
                                          for d in result['structure']['diagnostics'])
            return result
        except (KeyError, TypeError, ValueError, IndexError, AttributeError, OverflowError, RecursionError):
            raise NormalizationError() from None

    def _convert(self, native, document_id, context, rotations):
        if not isinstance(native, dict) or not isinstance(context, ImportContext) or not isinstance(document_id, str) or not re.fullmatch(r"doc_[0-9a-f]{64}", document_id) or not isinstance(rotations, dict):
            raise ValueError
        # Reject SDK objects and non-JSON values without copying them into output.
        json.dumps(native, allow_nan=False)
        pages, blocks, tables, warnings = [], [], [], []

        def warn(code, source_ref, **detail):
            warnings.append({"code": code, "source_ref": source_ref, **detail})

        if not isinstance(native["pages"], dict) or not native["pages"]:
            raise ValueError
        for key, value in sorted(native["pages"].items(), key=lambda x: int(x[0])):
            page_no = int(key)
            if page_no < 1 or str(page_no) != key or value["page_no"] != page_no:
                raise ValueError
            width, height = value["size"]["width"], value["size"]["height"]
            if not _number(width) or not _number(height) or width <= 0 or height <= 0:
                raise ValueError
            rotation = rotations.get(page_no)
            if rotation is not None and (type(rotation) is not int or rotation not in (0, 90, 180, 270)):
                raise ValueError
            pages.append({"page_no": page_no, "width": width, "height": height,
                          "coordinate_unit": "pdf_points", "rotation": rotation,
                          "location_status": "available" if rotation == 0 else "unavailable"})
            if rotation != 0:
                warn("PAGE_ROTATION_UNKNOWN" if rotation is None else "ROTATED_PAGE_UNSUPPORTED", f"#/pages/{key}")
        sizes = {p["page_no"]: p for p in pages}

        def location(prov, text, ref):
            reason = None
            if not isinstance(prov, dict):
                reason = "INVALID_PROVENANCE"
            else:
                page_no, bbox, span = prov.get("page_no"), prov.get("bbox"), prov.get("charspan")
                page = sizes.get(page_no) if type(page_no) is int else None
                if page is None:
                    reason = "PAGE_NOT_FOUND"
                elif page["rotation"] != 0:
                    reason = "PAGE_LOCATION_UNSUPPORTED"
                elif not isinstance(span, list) or len(span) != 2 or any(type(x) is not int for x in span) or not 0 <= span[0] <= span[1] <= len(text):
                    reason = "INVALID_CHARSPAN"
                elif not isinstance(bbox, dict) or not all(_number(bbox.get(k)) for k in ("l", "t", "r", "b")):
                    reason = "INVALID_BBOX"
                else:
                    w, h = page["width"], page["height"]
                    if bbox.get("coord_origin") == "BOTTOMLEFT":
                        coords = [bbox["l"] / w, (h - bbox["t"]) / h, bbox["r"] / w, (h - bbox["b"]) / h]
                    elif bbox.get("coord_origin") == "TOPLEFT":
                        coords = [bbox["l"] / w, bbox["t"] / h, bbox["r"] / w, bbox["b"] / h]
                    else:
                        coords = None
                        reason = "UNKNOWN_COORD_ORIGIN"
                    if coords is not None:
                        if not all(0 <= x <= 1 for x in coords) or not (coords[0] < coords[2] and coords[1] < coords[3]):
                            reason = "INVALID_BBOX"
                        else:
                            return {"page_no": page_no, "bbox": coords, "charspan": list(span),
                                    "raw_bbox": copy.deepcopy(bbox), "location_level": "region"}
            warn("LOCATION_UNAVAILABLE", ref, reason=reason)
            return None

        def resolve(ref):
            if not isinstance(ref, str) or not ref.startswith("#/"):
                raise ValueError
            value = native
            for token in ref[2:].split("/"):
                if isinstance(value, list):
                    if not token.isdigit(): raise ValueError
                    value = value[int(token)]
                else:
                    value = value[token.replace("~1", "/").replace("~0", "~")]
            if not isinstance(value, dict): raise ValueError
            if value.get("self_ref", ref) != ref: raise ValueError
            return value

        def emit(ref, obj, text, kind, provenance, **extra):
            if not isinstance(text, str) or not isinstance(provenance, list): raise ValueError
            locations = [location(p, text, ref) for p in provenance]
            if not provenance: warn("LOCATION_UNAVAILABLE", ref, reason="PROVENANCE_MISSING")
            block = {"block_id": f"{document_id}:block:{len(blocks)+1:06d}", "kind": kind,
                     "text": text, "source_ref": ref, "source_label": obj.get("label"),
                     "content_layer": obj.get("content_layer"), "parent_ref": obj.get("parent", {}).get("$ref"),
                     "locations": [p for p in locations if p is not None],
                     "location_status": "available" if locations and all(p is not None for p in locations) else "unavailable"}
            block.update(extra)
            if "orig" in obj: block["original_text"] = copy.deepcopy(obj["orig"])
            blocks.append(block)

        visited, active = set(), set()

        def walk(ref):
            if ref in active: raise ValueError
            if ref in visited:
                warn("REPEATED_SOURCE_REFERENCE", ref)
                return
            active.add(ref)
            obj = resolve(ref)
            family = ref.split("/")[1]
            if family == "texts":
                kind = "title" if obj.get("label") in ("title", "section_header") else "paragraph"
                emit(ref, obj, obj["text"], kind, obj.get("prov", []))
            elif family == "tables":
                data, prov = obj["data"], obj.get("prov", [])
                cells = data["table_cells"]
                if not isinstance(cells, list) or not isinstance(prov, list) or any(not isinstance(p, dict) for p in prov): raise ValueError
                rows, columns = data["num_rows"], data["num_cols"]
                if type(rows) is not int or type(columns) is not int or rows < 0 or columns < 0: raise ValueError
                page_no = prov[0].get("page_no") if len(prov) == 1 else None
                captions = obj.get("captions", [])
                tables.append({"table_id": ref, "source_ref": ref, "parent_ref": obj.get("parent", {}).get("$ref"),
                               "page_no": page_no, "num_rows": rows, "num_cols": columns,
                               "caption_refs": copy.deepcopy(captions), "native_data": copy.deepcopy(data),
                               "header_semantics": "native_flags_only_no_inferred_headers"})
                for i, cell in enumerate(cells):
                    cell_ref = f"{ref}/data/table_cells/{i}"
                    row, column = cell["start_row_offset_idx"], cell["start_col_offset_idx"]
                    row_span, col_span = cell["row_span"], cell["col_span"]
                    if any(type(x) is not int for x in (row, column, row_span, col_span)) or row < 0 or column < 0 or row_span < 1 or col_span < 1 or row + row_span > rows or column + col_span > columns: raise ValueError
                    if cell.get("end_row_offset_idx", row + row_span) != row + row_span or cell.get("end_col_offset_idx", column + col_span) != column + col_span: raise ValueError
                    if any(type(cell[k]) is not bool for k in ("column_header", "row_header")): raise ValueError
                    provenance = [{"page_no": page_no, "bbox": cell.get("bbox"), "charspan": [0, len(cell["text"])]}] if page_no is not None else []
                    emit(cell_ref, {**obj, "parent": {"$ref": ref}}, cell["text"], "table_cell", provenance,
                         table_id=ref, row=row, column=column, row_span=row_span, column_span=col_span,
                         column_header=cell["column_header"], row_header=cell["row_header"],
                         span_basis="application_owned_cell_text_not_native_table_provenance")
                if not any(c["column_header"] or c["row_header"] for c in cells): warn("HEADER_FLAGS_ABSENT", ref)
                if not captions: warn("CAPTION_LINK_ABSENT", ref)
                if len(prov) > 1: warn("TABLE_PAGE_AMBIGUOUS", ref)
            elif family == "pictures":
                # Geometry/relations live outside text blocks: old IDs and spans stay stable.
                if isinstance(obj.get("text"), str):
                    emit(ref, obj, obj['text'], 'other', obj.get('prov', []), native_content=copy.deepcopy(obj))
            elif family not in ("body", "furniture", "groups"):
                warn("UNSUPPORTED_CONTENT_TYPE", ref, content_type=family)
                if isinstance(obj.get("text"), str):
                    emit(ref, obj, obj["text"], "other", obj.get("prov", []), native_content=copy.deepcopy(obj))
            children = obj.get("children", [])
            if not isinstance(children, list): raise ValueError
            for child in children: walk(child["$ref"])
            active.remove(ref)
            visited.add(ref)

        walk("#/body")
        if "furniture" in native: walk("#/furniture")
        # Retain otherwise unreachable source text/content; flag its order as uncertain.
        families = ["texts", "tables", "pictures", "key_value_items", "form_items"]
        families.extend(k for k, v in native.items() if k not in families and k != "groups" and isinstance(v, list) and any(isinstance(item, dict) and "self_ref" in item for item in v))
        for family in families:
            items = native.get(family, [])
            if not isinstance(items, list): raise ValueError
            for i in range(len(items)):
                ref = f"#/{family}/{i}"
                if ref not in visited:
                    warn("UNREFERENCED_CONTENT", ref)
                    walk(ref)
        return {"document_id": document_id, "schema_version": "normalized-0.1",
                "source_kind": context.source_kind.value, "import_context": {"kind": context.kind.value, "bridge_id": context.bridge_id},
                "pages": pages, "blocks": blocks, "tables": tables, "warnings": warnings}
