"""Deterministic, fail-closed validator for the bounded inspection experiment.

No provider or golden-fact dependency. Text semantics are deliberately limited
to the declared Chinese fixture grammar, not a general professional assessor.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from provider import HERE, write_json

ROOT = HERE.parents[1]
SCHEMA = json.loads((ROOT / "specs/001-business-extraction/business.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
ENDS = {"HAS_COMPONENT": ("Bridge", "Component"), "HAS_DEFECT": ("Component", "Defect"), "HAS_OBSERVATION": ("Defect", "Observation")}
TYPE_TEXT = {"pothole": "坑槽", "exposed_rebar": "露筋", "deck_pavement": "桥面铺装", "girder": "主梁"}
CODE_FIELD = {"Bridge": "bridge_code", "Component": "component_code", "Defect": "defect_code", "Observation": "observation_code"}
UNITS = {"m²", "㎡", "m2"}
NEGATIVE = re.compile(r"未见|未发现|无明显|没有|未检出")
SUSPECTED = re.compile(r"疑似|可能|待确认|待核实")
AGGREGATE = re.compile(r"全桥|合计|累计|若干|多处|共\s*[2-9]\d*\s*处")


def compact(text):
    return re.sub(r"\s+", "", str(text))


def hash_id(prefix, value):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return prefix + hashlib.sha256(text.encode()).hexdigest()


def issue(code, message, ids=(), severity="reject"):
    return {"code": code, "severity": severity, "related_local_ids": list(ids), "message": message}


class DocumentIndex:
    def __init__(self, document):
        self.document = document
        self.blocks = {b["block_id"]: b for b in document["blocks"]}
        if len(self.blocks) != len(document["blocks"]):
            raise ValueError("Duplicate input block IDs")
        self.pages = {p["page_no"] for p in document["pages"]}

    def context(self, block_id):
        block = self.blocks[block_id]
        if block.get("kind") != "table_cell":
            return block["text"]
        table_id = block["table_id"]
        cells = [b for b in self.blocks.values() if b.get("table_id") == table_id]
        row = sorted((b for b in cells if b["row"] == block["row"]), key=lambda b: b["column"])
        column = [b for b in cells if b["row"] == 0 and b["column"] == block["column"]]
        # Explicitly bounded to this fixture's numbered tables. This mapping is
        # validated through table number and page, not native caption flags.
        match = re.fullmatch(r"#/tables/(\d+)", table_id)
        number = int(match[1]) + 1 if match else None
        page = block["locations"][0]["page_no"] if block.get("locations") else None
        titles = [b["text"] for b in self.blocks.values() if b.get("kind") != "table_cell"
                  and re.match(rf"表\s*{number}\s", b["text"])
                  and any(loc["page_no"] == page for loc in b.get("locations", []))]
        return " | ".join(titles + [b["text"] for b in column + row])

    def resolve(self, source, owner, allowed):
        if source["target_field"] not in allowed:
            raise ValueError("EVIDENCE_TARGET_FIELD: unknown target field")
        block = self.blocks.get(source["block_id"])
        if block is None:
            raise ValueError("EVIDENCE_BLOCK_MISSING: source block does not exist")
        quote = source["exact_quote"]
        positions = [match.start() for match in re.finditer(re.escape(quote), block["text"])]
        if not positions:
            raise ValueError("EVIDENCE_QUOTE_MISMATCH: quote is not an exact source substring")
        hint = source["occurrence_hint"]
        if len(positions) > 1 and hint is None:
            raise ValueError("EVIDENCE_AMBIGUOUS: repeated quote needs occurrence_hint")
        hint = 0 if hint is None else hint
        if hint >= len(positions):
            raise ValueError("EVIDENCE_OCCURRENCE: occurrence_hint is out of range")
        start, end = positions[hint], positions[hint] + len(quote)
        locations = [loc for loc in block.get("locations", [])
                     if loc.get("charspan") is not None and loc["charspan"][0] <= start < end <= loc["charspan"][1]]
        if len(locations) != 1:
            raise ValueError("EVIDENCE_LOCATION: quote has no unique verified location")
        loc = locations[0]
        box = loc["bbox"]
        if len(box) != 4 or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1) or loc["page_no"] not in self.pages:
            raise ValueError("EVIDENCE_BBOX: invalid source position")
        value = {"document_id": self.document["document_id"], "block_id": block["block_id"], "page_no": loc["page_no"], "bbox": [round(x, 6) for x in box], "charspan": [start, end], "quote": quote, "target_field": source["target_field"], "location_level": "region", "source_ref": block["source_ref"]}
        value["evidence_id"] = hash_id("ev_", value)
        return value


def supports(field, value, sources, index):
    if value is None:
        return True
    matching = [s for s in sources if s["target_field"] == field]
    if not matching:
        return False
    quotes = [s["exact_quote"] for s in matching]
    contexts = [index.context(s["block_id"]) for s in matching]
    if field in ("defect_type", "component_type"):
        return value in TYPE_TEXT and any(TYPE_TEXT[value] in text for text in quotes)
    if field == "area_original_value":
        for source, q in zip(matching, quotes):
            block = index.blocks[source["block_id"]]
            if block.get("kind") == "table_cell":
                headers = [b["text"] for b in index.blocks.values() if b.get("table_id") == block["table_id"] and b.get("row") == 0 and b.get("column") == block["column"]]
                if not any("面积" in header for header in headers):
                    continue
                tokens = re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", q)
            else:
                # A numeric-only quote is valid when the authoritative block
                # explicitly identifies the same number as an area. Do not
                # mistake another number in that block (e.g. count) for area.
                contextual = re.findall(r"(?:面积\s*(?:为|约|=|：|:)\s*|S\s*=\s*)(\d+(?:\.\d+)?)", block["text"])
                quoted_tokens = re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", q)
                tokens = [token for token in contextual if any(Decimal(token) == Decimal(other) for other in quoted_tokens)]
            if any(Decimal(token) == Decimal(value) for token in tokens):
                return True
        return False
    if field == "area_original_unit":
        return any(value in q or (compact(q) in UNITS and value == compact(q)) for q in quotes)
    if field == "reported_count":
        return any(compact(q) == str(value) or re.search(rf"(?<!\d){value}\s*处", q) for q in quotes)
    if field == "observed_at":
        return any(value in q and re.search(r"观测日期|检测日期|检查日期", ctx)
                   and not re.search(r"编制|未知|未提供", ctx) for q, ctx in zip(quotes, contexts))
    if field == "assertion":
        return value == "positive" and any(re.search(r"有\s*1\s*处|发现\s*1\s*处", q)
                   and not NEGATIVE.search(q) and not SUSPECTED.search(q) for q in quotes)
    if field == "scope":
        return value == "single_defect" and any(re.search(r"1\s*处", q) and not AGGREGATE.search(q) for q in quotes)
    if field == "area_is_approximate":
        for q in quotes:
            if re.search(r"面积|m²|㎡|m2", q):
                approximate = bool(re.search(r"约|大约|近似|左右|超过|小于|[<>≤≥]", q))
                if approximate == value:
                    return True
        return False
    return any(compact(value) in compact(q) for q in quotes)


def deduplicate(candidate, diagnostics):
    entities, aliases, grouped = [], {}, {}
    for entity in candidate["entities"]:
        code = entity["data"][CODE_FIELD[entity["entity_type"]]]
        key = (entity["entity_type"], code) if code is not None else (entity["entity_type"], entity["local_id"])
        if key not in grouped:
            clone = copy.deepcopy(entity)
            # Observations with explicit codes have deterministic canonical
            # local IDs even if a subsequent extraction changes naming style.
            if entity["entity_type"] == "Observation" and code is not None:
                clone["local_id"] = "Observation:" + code
            grouped[key] = clone
            entities.append(clone)
        else:
            clone = grouped[key]
            if clone["data"] != entity["data"]:
                diagnostics.append(issue("DUPLICATE_CONFLICT", "Same explicit code has conflicting field values", [entity["local_id"], clone["local_id"]]))
            else:
                clone["sources"].extend(copy.deepcopy(entity["sources"]))
                diagnostics.append(issue("DUPLICATE_MERGED", "Same explicit code merged before identity generation", [entity["local_id"], clone["local_id"]], "info"))
        aliases[entity["local_id"]] = clone["local_id"]
    relations, relation_keys = [], {}
    for relation in candidate["relations"]:
        clone = copy.deepcopy(relation)
        clone["from_ref"] = aliases.get(clone["from_ref"], clone["from_ref"])
        clone["to_ref"] = aliases.get(clone["to_ref"], clone["to_ref"])
        key = (clone["relation_type"], clone["from_ref"], clone["to_ref"])
        if key in relation_keys:
            relation_keys[key]["sources"].extend(clone["sources"])
        else:
            relation_keys[key] = clone
            relations.append(clone)
    statements = copy.deepcopy(candidate["statements"])
    for statement in statements:
        if statement["component_ref"] is not None:
            statement["component_ref"] = aliases.get(statement["component_ref"], statement["component_ref"])
    return entities, relations, statements, aliases


def validate_candidate(candidate, document):
    diagnostics, evidence = [], {}
    errors = list(VALIDATOR.iter_errors(candidate))
    if errors or candidate.get("stage") != "candidate":
        return {"records": None, "diagnostics": [issue("SCHEMA_INVALID", "Candidate failed JSON Schema or stage check")], "schema_error_count": len(errors)}
    index = DocumentIndex(document)
    for field in ("document_id", "source_kind"):
        if candidate[field] != document[field]:
            diagnostics.append(issue("SOURCE_CONTEXT_MISMATCH", f"{field} differs from trusted input"))
    original_ids = [e["local_id"] for e in candidate["entities"]]
    if len(set(original_ids)) != len(original_ids):
        diagnostics.append(issue("LOCAL_ID_DUPLICATE", "Candidate local IDs must be unique"))
    entities, relations, statements, aliases = deduplicate(candidate, diagnostics)
    local = {e["local_id"]: e for e in entities}
    if len(local) != len(entities):
        diagnostics.append(issue("CANONICAL_ID_COLLISION", "Canonical local IDs collide after explicit-code deduplication"))
    parent, incoming = {}, {}
    for relation in relations:
        left, right = local.get(relation["from_ref"]), local.get(relation["to_ref"])
        if left is None or right is None:
            diagnostics.append(issue("RELATION_REF_MISSING", "Relation endpoint does not exist", [relation["from_ref"], relation["to_ref"]]))
        elif (left["entity_type"], right["entity_type"]) != ENDS[relation["relation_type"]]:
            diagnostics.append(issue("RELATION_END_TYPE", "Relation endpoint type is invalid", [left["local_id"], right["local_id"]]))
        else:
            incoming.setdefault(right["local_id"], []).append(relation)
    for entity in entities:
        if entity["entity_type"] == "Bridge":
            continue
        parents = incoming.get(entity["local_id"], [])
        if len(parents) != 1:
            diagnostics.append(issue("PARENT_CARDINALITY", "Non-bridge entity requires exactly one parent", [entity["local_id"]]))
        else:
            parent[entity["local_id"]] = parents[0]["from_ref"]
    # Endpoint types strictly decrease through the fixed hierarchy; together
    # with one-parent checks they exclude cycles without trusting model IDs.
    if any(d["severity"] == "reject" for d in diagnostics):
        return {"records": None, "diagnostics": diagnostics}

    entity_evidence = {e["local_id"]: [] for e in entities}
    def resolve_sources(sources, owner, allowed):
        resolved = []
        for source in sources:
            try:
                item = index.resolve(source, owner, allowed)
                evidence[item["evidence_id"]] = item
                resolved.append(item["evidence_id"])
            except ValueError as error:
                code, _, message = str(error).partition(": ")
                diagnostics.append(issue(code, message, [owner] if owner else []))
        return resolved

    pending_area = set()
    for entity in entities:
        owner, data, kind = entity["local_id"], entity["data"], entity["entity_type"]
        entity_evidence[owner] += resolve_sources(entity["sources"], owner, set(data))
        for field, value in data.items():
            if kind == "Observation" and field in ("assertion", "scope") and value not in ("positive", "single_defect"):
                diagnostics.append(issue("NON_POSITIVE_OR_AGGREGATE", "Observation belongs in raw statements or pending candidates", [owner], "pending"))
                continue
            try:
                supported = supports(field, value, entity["sources"], index)
            except (KeyError, ValueError):
                supported = False
            if not supported:
                diagnostics.append(issue("FIELD_UNSUPPORTED", f"{field} has no matching field-specific source support", [owner]))
        if kind == "Bridge" and (not data["name"] or not data["bridge_code"]):
            diagnostics.append(issue("IDENTITY_UNRESOLVED", "Bridge identity requires an explicit evidenced code", [owner], "pending"))
        if kind == "Component" and (not data["name"] or not data["component_code"]):
            diagnostics.append(issue("IDENTITY_UNRESOLVED", "Component requires name and explicit code", [owner], "pending"))
        if kind == "Defect" and (data["defect_type"] == "unknown" or not data["location_text"] or not data["defect_type_raw"]):
            diagnostics.append(issue("DEFECT_UNRESOLVED", "Defect type/location cannot be safely represented", [owner], "pending"))
        if kind == "Observation":
            description = data["description"]
            if data["assertion"] == "positive" and (NEGATIVE.search(description) or SUSPECTED.search(description)):
                diagnostics.append(issue("ASSERTION_CONTRADICTION", "Positive observation description is negative or suspected", [owner]))
            if data["scope"] == "single_defect" and (AGGREGATE.search(description) or data["reported_count"] not in (None, 1)):
                diagnostics.append(issue("SCOPE_CONTRADICTION", "Single defect observation contains aggregate/count contradiction", [owner]))
            own_code = data["observation_code"]
            for source in entity["sources"]:
                block = index.blocks.get(source["block_id"])
                if block is None:
                    continue
                if source["target_field"] in ("area_original_value", "reported_count", "description") and block.get("kind") == "table_cell" and block.get("row") != 0:
                    if own_code is None or own_code not in index.context(source["block_id"]):
                        diagnostics.append(issue("FIELD_ROW_MISMATCH", "Observation field evidence belongs to a different or unidentified table row", [owner]))
                if source["target_field"] == "area_original_value" and block.get("kind") != "table_cell":
                    description_blocks = {s["block_id"] for s in entity["sources"] if s["target_field"] == "description"}
                    if source["block_id"] not in description_blocks:
                        diagnostics.append(issue("FIELD_PARAGRAPH_MISMATCH", "Paragraph area evidence is not the observation's description source", [owner]))
            defect = local[parent[owner]]["data"]
            if defect["location_text"] and compact(defect["location_text"]) not in compact(description):
                diagnostics.append(issue("OBSERVATION_LOCATION_MISMATCH", "Observation description does not support its defect location", [owner]))
            if defect["defect_type"] in TYPE_TEXT and TYPE_TEXT[defect["defect_type"]] not in description:
                diagnostics.append(issue("OBSERVATION_TYPE_MISMATCH", "Observation description does not support its defect type", [owner]))
            if data["area_original_value"] is None or data["area_original_unit"] not in UNITS:
                pending_area.add(owner)
                diagnostics.append(issue("AREA_UNNORMALIZABLE", "Area is missing or its evidenced unit is unsupported", [owner], "pending"))

    relation_fields = {"bridge_id", "component_id", "defect_id", "observation_id", "bridge_code", "component_code", "defect_code", "observation_code", "name", "location_text", "description"}
    for relation in relations:
        owner = relation["to_ref"]
        entity_evidence[owner] += resolve_sources(relation["sources"], owner, relation_fields)
        left, right = local[relation["from_ref"]], local[owner]
        values = [e["data"][CODE_FIELD[e["entity_type"]]] for e in (left, right)]
        contexts = []
        for source in relation["sources"]:
            if source["block_id"] in index.blocks:
                contexts.append(index.context(source["block_id"]))
        if any(value is None for value in values) or not any(all(compact(value) in compact(ctx) for value in values) for ctx in contexts):
            diagnostics.append(issue("RELATION_UNSUPPORTED", "No proposed source context ties both explicit endpoint codes together", [relation["from_ref"], owner]))

    for statement in statements:
        resolve_sources(statement["sources"], statement["component_ref"], {"description", "kind", "location_text", "component_ref"})
        if statement["component_ref"] is not None and (statement["component_ref"] not in local or local[statement["component_ref"]]["entity_type"] != "Component"):
            diagnostics.append(issue("STATEMENT_REF_INVALID", "Statement component reference is invalid"))
        if not any(statement["text"] in source["exact_quote"] for source in statement["sources"]):
            diagnostics.append(issue("STATEMENT_TEXT_UNSUPPORTED", "Statement text is not supported by its exact quote"))
        if statement["kind"] == "negative" and not NEGATIVE.search(statement["text"]):
            diagnostics.append(issue("STATEMENT_KIND_UNSUPPORTED", "Negative statement has no supported negative marker"))
        component = local.get(statement["component_ref"])
        if component is not None and component["entity_type"] == "Component":
            component_type = component["data"]["component_type"]
            marker = {"deck_pavement": "铺装", "girder": "主梁"}.get(component_type)
            if marker and marker not in statement["text"]:
                diagnostics.append(issue("STATEMENT_COMPONENT_UNSUPPORTED", "Statement text does not support the proposed component"))

    blocking = [d for d in diagnostics if d["severity"] == "reject" or (d["severity"] == "pending" and d["code"] != "AREA_UNNORMALIZABLE")]
    if blocking:
        return {"records": None, "diagnostics": diagnostics, "deduplicated_entities": len(entities), "aliases": aliases}

    records = {k: candidate[k] for k in ("schema_version", "document_id", "source_kind")}
    records.update(stage="records", bridges=[], components=[], defects=[], observations=[], evidence=[], statements=[], issues=diagnostics.copy())
    ids = {}
    # Generate parent IDs first, without relying on model entity ordering.
    for kind in ("Bridge", "Component", "Defect", "Observation"):
        for entity in entities:
            if entity["entity_type"] != kind:
                continue
            owner, data = entity["local_id"], copy.deepcopy(entity["data"])
            common = {"validation_status": "passed", "acceptance_status": "pending_review" if owner in pending_area else "accepted", "review_status": "unreviewed", "quality_mode": "demo_accept_all", "quality_score": None, "source_document_id": document["document_id"], "evidence_ids": sorted(set(entity_evidence[owner]))}
            if kind == "Bridge":
                ids[owner] = data["bridge_code"]
                data["bridge_id"] = ids[owner]
                records["bridges"].append({**data, **common})
            elif kind == "Component":
                ids[owner] = hash_id("cmp_", [ids[parent[owner]], data["component_code"]])
                data.update(component_id=ids[owner], bridge_id=ids[parent[owner]])
                records["components"].append({**data, **common})
            elif kind == "Defect":
                ids[owner] = hash_id("def_", [document["document_id"], data["defect_code"] or owner])
                data.update(defect_id=ids[owner], component_id=ids[parent[owner]], identity_scope="document_local")
                records["defects"].append({**data, **common})
            else:
                ids[owner] = hash_id("obs_", [document["document_id"], owner])
                canonical_area = None
                if owner not in pending_area:
                    canonical_area = format(Decimal(data["area_original_value"]), "f")
                data.update(observation_id=ids[owner], defect_id=ids[parent[owner]], component_id=ids[parent[parent[owner]]], source_kind=document["source_kind"], area_value=canonical_area, area_unit="m²", area_conversion="identity" if canonical_area is not None else None)
                optional = ("observed_at", "reported_count", "area_original_value", "area_original_unit", "area_value", "area_is_approximate")
                data["missing_fields"] = [field for field in optional if data[field] is None]
                records["observations"].append({**data, **common})
    for statement in statements:
        if statement["component_ref"] is not None:
            statement["component_ref"] = ids[statement["component_ref"]]
        records["statements"].append(statement)
    records["evidence"] = sorted(evidence.values(), key=lambda value: value["evidence_id"])
    record_errors = validate_records(records, document)
    if record_errors:
        diagnostics.extend(record_errors)
        return {"records": None, "diagnostics": diagnostics}
    return {"records": records, "diagnostics": diagnostics, "aliases": aliases, "deduplicated_entities": len(entities)}


def validate_records(records, document):
    diagnostics = []
    if list(VALIDATOR.iter_errors(records)) or records.get("stage") != "records":
        return [issue("RECORD_SCHEMA_INVALID", "Record bundle failed schema/stage check")]
    index = DocumentIndex(document)
    if any(records[field] != document[field] for field in ("document_id", "source_kind")):
        diagnostics.append(issue("RECORD_SOURCE_MISMATCH", "Record source context differs from input"))
    maps = {}
    for plural, key in (("bridges", "bridge_id"), ("components", "component_id"), ("defects", "defect_id"), ("observations", "observation_id"), ("evidence", "evidence_id")):
        maps[plural] = {value[key]: value for value in records[plural]}
        if len(maps[plural]) != len(records[plural]):
            diagnostics.append(issue("RECORD_ID_DUPLICATE", f"Duplicate {plural} IDs"))
    for evidence in records["evidence"]:
        block = index.blocks.get(evidence["block_id"])
        box, span = evidence["bbox"], evidence["charspan"]
        valid = block is not None and evidence["document_id"] == document["document_id"] and evidence["source_ref"] == block["source_ref"]
        valid &= 0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1 and evidence["page_no"] in index.pages
        if block is not None:
            valid &= any(loc["page_no"] == evidence["page_no"] and all(abs(a-b) <= 0.000001 for a, b in zip(box, loc["bbox"])) for loc in block["locations"])
            valid &= span is not None and 0 <= span[0] < span[1] <= len(block["text"]) and block["text"][span[0]:span[1]] == evidence["quote"]
        if not valid:
            diagnostics.append(issue("RECORD_EVIDENCE_INVALID", "Evidence position/text differs from authoritative snapshot"))
    for plural in ("bridges", "components", "defects", "observations"):
        for record in records[plural]:
            if record["source_document_id"] != document["document_id"] or not set(record["evidence_ids"]) <= set(maps["evidence"]):
                diagnostics.append(issue("RECORD_EVIDENCE_REF", "Record evidence/document references do not resolve"))
            sources = [{"block_id": maps["evidence"][ref]["block_id"], "exact_quote": maps["evidence"][ref]["quote"], "target_field": maps["evidence"][ref]["target_field"], "occurrence_hint": None}
                       for ref in record["evidence_ids"] if ref in maps["evidence"]]
            definition = {"bridges": "BridgeCandidateData", "components": "ComponentCandidateData", "defects": "DefectCandidateData", "observations": "ObservationCandidateData"}[plural]
            for field in SCHEMA["$defs"][definition]["required"]:
                try:
                    supported = supports(field, record[field], sources, index)
                except (KeyError, ValueError):
                    supported = False
                if not supported:
                    diagnostics.append(issue("RECORD_FIELD_UNSUPPORTED", f"Record {field} is not supported by referenced evidence"))
    for component in records["components"]:
        if component["bridge_id"] not in maps["bridges"]:
            diagnostics.append(issue("RECORD_PARENT_REF", "Component bridge does not exist"))
    for defect in records["defects"]:
        if defect["component_id"] not in maps["components"]:
            diagnostics.append(issue("RECORD_PARENT_REF", "Defect component does not exist"))
    for observation in records["observations"]:
        defect = maps["defects"].get(observation["defect_id"])
        if defect is None or observation["component_id"] != defect["component_id"] or observation["source_kind"] != document["source_kind"]:
            diagnostics.append(issue("RECORD_PARENT_REF", "Observation component/defect/source linkage is inconsistent"))
        if observation["area_value"] is not None:
            if observation["area_original_unit"] not in UNITS or observation["area_conversion"] != "identity" or Decimal(observation["area_value"]) != Decimal(observation["area_original_value"]):
                diagnostics.append(issue("RECORD_AREA_INCONSISTENT", "Normalized area differs from original value/unit"))
        if observation["observed_at"] is None and "observed_at" not in observation["missing_fields"]:
            diagnostics.append(issue("RECORD_MISSING_FIELD", "Unknown date lacks missing_fields entry"))
    return diagnostics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_folder", type=Path)
    args = parser.parse_args()
    folder = args.run_folder
    candidate = json.loads((folder / "candidate.json").read_text())
    document = json.loads((folder / "input-document.json").read_text())
    result = validate_candidate(candidate, document)
    records = result.pop("records")
    result["status"] = "records_generated" if records is not None else "candidate_retained_without_records"
    if records is not None:
        write_json(folder / "records.json", records)
        result["counts"] = {key: len(records[key]) for key in ("bridges", "components", "defects", "observations", "evidence", "statements")}
    else:
        # A failed revalidation must not leave a previously generated bundle
        # available for evaluation as though it belonged to this candidate.
        (folder / "records.json").unlink(missing_ok=True)
    write_json(folder / "validation.json", result)
    print(json.dumps(result, ensure_ascii=False))
    if records is None:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
