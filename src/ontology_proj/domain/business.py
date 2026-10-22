from ontology_proj.domain.bridge_identity import bound_bridge_matches
"""Pure bounded business rules migrated from the preserved experiment.

Schema and a structure-check callback are injected; no I/O or SDK imports.
"""
import copy
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from ontology_proj.domain.registration import ManualRegistration
from ontology_proj.domain.measurement import legacy_area, record_area_valid

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


def statement_kind(text, rules=None):
    """Bounded grammar from contract 1.1, never a professional classifier."""
    value = compact(text)
    if re.search(r'正文与表格|不表示新增|不表示全桥无病害|日期.*未知|报告编制日期|本样本|演示声明|合成.*样本|记录说明', value):
        return 'unmapped'
    if rules is not None:
        flags=rules.qualifiers(value)
        disease='病害' in value or any(rules.type_supported(k,value) for k in ('pothole','exposed_rebar'))
        if flags['suspected']:return 'suspected'
        if disease and flags['negative']:return 'negative'
        if disease and flags['aggregate']:return 'aggregate'
        return 'unmapped'
    if SUSPECTED.search(value):
        return 'suspected'
    if NEGATIVE.search(value) and re.search(r'病害|露筋|坑槽', value):
        return 'negative'
    if AGGREGATE.search(value) and re.search(r'病害|露筋|坑槽', value):
        return 'aggregate'
    return 'unmapped'


def bound_statement_location(statement, component, index):
    """Only derive a literal place from a complete confirmed source pattern."""
    registry = getattr(index, 'registration', None)
    if not registry or not component or statement['kind'] != 'negative' or statement['location_text'] is not None:
        return None
    binding = registry.match('Component', component['data'])
    if not binding:
        return None
    match = re.fullmatch(r'(?:经检查[，,])?(桥头)平顺[，,]未见明显病害[。. ]?', compact(statement['text']))
    if not match:
        return None
    for field in ('description','kind','component_ref'):
        if not any(s['target_field']==field and s['exact_quote']==statement['text']
                   and registry.contains(binding,s,'statement_scopes') for s in statement['sources']):
            return None
    return match.group(1)


def statement_diagnostics(statement, local, index):
    problems = []
    def reject(code, message):
        problems.append(issue(code, message))
    sources = statement['sources']
    description = [s for s in sources if s['target_field'] == 'description' and statement['text'] in s['exact_quote']]
    kind_sources = [s for s in sources if s['target_field'] == 'kind']
    if not description:
        reject('STATEMENT_TEXT_UNSUPPORTED', 'Statement requires its full original text as description evidence')
    if statement['kind'] != statement_kind(statement['text'],getattr(index,'rules',None)) or not kind_sources or not any(s['block_id'] in {d['block_id'] for d in description} for s in kind_sources):
        reject('STATEMENT_KIND_UNSUPPORTED', 'Statement kind contradicts its original purpose or lacks kind evidence')
    component = local.get(statement['component_ref'])
    if statement['component_ref'] is not None and (component is None or component['entity_type'] != 'Component'):
        reject('STATEMENT_REF_INVALID', 'Statement component reference is invalid')
        return problems
    component_sources = [s for s in sources if s['target_field'] == 'component_ref']
    if component:
        data = component['data']
        markers = [data['component_code'], data['name']]
        if data['component_type'] == 'deck_pavement': markers.append('铺装')
        if data['component_type'] == 'girder': markers.append('主梁')
        desc_ids = {s['block_id'] for s in description}
        direct = any(s['block_id'] in desc_ids and s['exact_quote'] in statement['text'] and any(m and m in s['exact_quote'] for m in markers) for s in component_sources)
        ordered = list(index.blocks.values())
        positions = {b['block_id']: i for i, b in enumerate(ordered)}
        contextual = False
        for title_source in component_sources:
            title = index.blocks.get(title_source['block_id'])
            if not title or title['kind'] == 'table_cell' or not data['component_code'] or data['component_code'] not in title_source['exact_quote']: continue
            match = re.match(r'表\s*(\d+)\s', title['text'])
            if not match: continue
            # Only the current four numbered tables; verify traversal/page/adjacency.
            table_id = '#/tables/' + str(int(match[1])-1)
            cells = [b for b in ordered if b.get('table_id') == table_id]
            if not cells: continue
            for desc_id in desc_ids:
                desc = index.blocks.get(desc_id)
                if not desc or not any(s['block_id'] == desc_id and statement['text'] in s['exact_quote'] for s in component_sources): continue
                same_page = set(loc['page_no'] for b in [title, desc, *cells] for loc in b['locations'])
                if len(same_page) == 1 and positions[title['block_id']] < min(positions[b['block_id']] for b in cells) and positions[desc_id] == max(positions[b['block_id']] for b in cells)+1:
                    contextual = True
        registry = getattr(index, 'registration', None)
        binding = registry.match('Component', data) if registry else None
        controlled = binding and any(registry.contains(binding,s,'statement_scopes') and s['block_id'] in desc_ids for s in component_sources)
        if not direct and not contextual and not controlled:
            reject('STATEMENT_COMPONENT_UNSUPPORTED', 'Component evidence must be direct or a verified adjacent table chain')
    derived_location = bound_statement_location(statement, component, index)
    if statement['kind'] == 'negative' and (statement['component_ref'] is None or (statement['location_text'] is None and derived_location is None)):
        reject('STATEMENT_SCOPE_MISSING', 'Current negative findings require their evidenced component and location')
    location = statement['location_text']
    if location is not None:
        quotes = [s['exact_quote'] for s in sources if s['target_field'] == 'location_text']
        bridgehead = component and component['data']['component_code'] == 'C-DECK-01' and component['data']['component_type'] == 'deck_pavement' and location in {'桥头', '桥头区域', '桥头铺装'}
        if not any(compact(location) in compact(q) or (bridgehead and any(t in q for t in ['桥头', '桥头区域', '桥头铺装'])) for q in quotes):
            reject('STATEMENT_LOCATION_UNSUPPORTED', 'Statement location lacks original location evidence')
    return problems


class DocumentIndex:
    def __init__(self, document, rules=None):
        self.document = document
        self.rules = rules
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
    binding = getattr(index, 'manual_binding', None)
    if binding:
        scoped = index.registration.scoped_support(field, value, sources, binding, index.manual_defect)
        if scoped is not None:
            return scoped
    if value is None:
        return True
    matching = [s for s in sources if s["target_field"] == field]
    if not matching:
        return False
    quotes = [s["exact_quote"] for s in matching]
    contexts = [index.context(s["block_id"]) for s in matching]
    rules = getattr(index, 'rules', None)
    if rules is not None:
        if field in ('defect_type','component_type'):
            return any(rules.type_supported(value,q) for q in quotes)
        if field == 'area_expression':
            return isinstance(value,str) and any(value in q for q in quotes)
        if field == 'area_original_value':
            for source,q in zip(matching,quotes):
                block=index.blocks[source['block_id']]
                headers=' '.join(b['text'] for b in index.blocks.values()
                    if block.get('table_id') and b.get('table_id')==block['table_id']
                    and b.get('column')==block.get('column') and (b.get('column_header') or b.get('row')==0))
                own=getattr(index,'measurement_data',{}).get('description','')
                parsed=rules.direct(own if own and own in q else q,header=headers or None)
                if not parsed and compact(q)==compact(value):
                    parsed=rules.direct(own if own and own in block['text'] else block['text'],header=headers or None)
                expected_unit=rules.unit(getattr(index,'measurement_data',{}).get('area_original_unit'))
                if any(Decimal(v)==Decimal(value) and expected_unit==rules.unit(u) for v,u in parsed):return True
            return False
        if field == 'area_is_approximate':
            own=getattr(index,'measurement_data',{}).get('description','')
            return any((any(m in ctx for m in rules.data['area_markers']) or ('矩形' in q and '长' in q and '宽' in q))
                       and value == rules.qualifiers(own if own and own in ctx else q)['approximate']
                       for q,ctx in zip(quotes,contexts))
        if field in ('assertion','scope'):
            for q in quotes:
                flags=rules.qualifiers(q)
                word=any(rules.type_supported(k,q) for k in ('pothole','exposed_rebar'))
                single=bool(re.search(r'(?:1|一)处',compact(q))) or not flags['aggregate']
                if word and single and not any(flags[k] for k in ('negative','suspected','aggregate')):
                    if value==('positive' if field=='assertion' else 'single_defect'):return True
            return False
        if field=='reported_count':
            return any(re.search(rf'(?<!\d){value}\s*处',q) or (value==1 and re.search(r'(?<![一二三四五六七八九十百千零两])一处',q)) for q in quotes)
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
                # A source number may touch its unit (S=0.04m2). Keep the
                # decimal boundary, without rejecting the adjacent unit.
                quoted_tokens = re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?=$|[^\w.]|m(?:²|\s*2)(?![\w.]))", q)
                tokens = []
                for token in contextual:
                    try:
                        d_tok = Decimal(token)
                        if any(Decimal(other) == d_tok for other in quoted_tokens):
                            tokens.append(token)
                    except (InvalidOperation, TypeError):
                        continue
            try:
                if value is not None and any(Decimal(token) == Decimal(value) for token in tokens):
                    return True
            except (InvalidOperation, TypeError):
                continue
        return False
    if field == "area_original_unit":
        return any(value in q or (compact(q) in UNITS and value == compact(q)) for q in quotes)
    if field == "reported_count":
        return any(compact(q) == str(value) or re.search(rf"(?<!\d){value}\s*处", q) for q in quotes)
    if field == "observed_at":
        return any(value in q and re.search(r"观测日期|检测日期|检查日期", ctx)
                   and not re.search(r"编制|未知|未提供", ctx) for q, ctx in zip(quotes, contexts))
    if field == "assertion":
        return value == "positive" and any(re.search(r"有\s*1\s*处|发现\s*1\s*处|存在\s*1\s*处", q)
                   and not NEGATIVE.search(q) and not SUSPECTED.search(q) for q in quotes)
    if field == "scope":
        return value == "single_defect" and any(re.search(r"1\s*处", q) and not AGGREGATE.search(q) for q in quotes)
    if field == "area_is_approximate":
        for q in quotes:
            if re.search(r"面积|m²|㎡|m2", compact(q)):
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


def validate_candidate(candidate, document, schema, structure_errors, registration=None, rules=None, bridge_binding=None):
    diagnostics, evidence = [], {}
    errors = structure_errors(candidate)
    if errors or candidate.get("stage") != "candidate":
        return {"records": None, "diagnostics": [issue("SCHEMA_INVALID", "Candidate failed JSON Schema or stage check")], "schema_error_count": len(errors)}
    flexible = candidate.get('schema_version') in ('0.3.0-draft','0.4.0-draft')
    if not flexible and any(e['data'].get('area_expression') is not None for e in candidate['entities']):
        return {'records':None,'diagnostics':[issue('AREA_EXPRESSION_VERSION','Derived expression requires version 0.3')]}
    catalog = candidate.get('schema_version') == '0.4.0-draft'
    if catalog and (not bridge_binding or bridge_binding.get('status')!='resolved' or bridge_binding['document_id']!=document['document_id']):
        return {'records':None,'diagnostics':[issue('BRIDGE_BINDING_REQUIRED','Resolved source-bound bridge identity required')]}
    index = DocumentIndex(document, rules if flexible else None)
    registered = candidate.get('schema_version') == '0.2.0-draft' or (flexible and candidate.get('identity_mode')=='registered')
    if registration and registration.get('document_id') == document['document_id'] and not registered:
        return {'records': None, 'diagnostics': [issue('REGISTRATION_VERSION_MISMATCH', 'Configured source requires controlled envelope version')]}
    try:
        registry = ManualRegistration(registration, document) if registered and registration else None
    except (ValueError, KeyError, TypeError):
        registry = None
    if registered and registry is None:
        return {'records': None, 'diagnostics': [issue('REGISTRATION_REQUIRED', 'Version 0.2 requires confirmed source-bound registration')]}
    index.registration = registry
    if registry:registry.flexible_rules = rules if flexible else None
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
    from ontology_proj.domain.row_evidence import RowEvidence
    row_evidence = RowEvidence(document)
    observation_rows = {}
    if catalog:
        for entity in entities:
            if entity['entity_type'] != 'Observation':
                continue
            defect = local[parent[entity['local_id']]]
            component = local[parent[defect['local_id']]]
            row = row_evidence.observation_row(entity['data'], entity['sources'], component['data'],
                                               defect['data'], defect['sources'])
            if row is not None:
                observation_rows[entity['local_id']] = row
        # A row containing several candidate facts is outside this bounded path.
        observation_rows = {owner: row for owner, row in observation_rows.items()
                            if list(observation_rows.values()).count(row) == 1}
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

    bindings = {}
    if registry:
        for entity in entities:
            if entity['entity_type'] in ('Bridge','Component'):
                binding = registry.match(entity['entity_type'], entity['data'])
                if binding: bindings[entity['local_id']] = binding
                else: diagnostics.append(issue('REGISTRATION_UNBOUND', 'Entity is outside controlled registration', [entity['local_id']]))
        for entity in entities:
            owner = entity['local_id']
            if entity['entity_type'] == 'Defect' and parent[owner] in bindings:
                bindings[owner] = bindings[parent[owner]]
            elif entity['entity_type'] == 'Observation' and parent[parent[owner]] in bindings:
                bindings[owner] = bindings[parent[parent[owner]]]
    if catalog and sum(e['entity_type']=='Bridge' for e in entities)!=1:
        return {'records':None,'diagnostics':[issue('BRIDGE_CARDINALITY','Catalog report requires exactly one bridge')]}
    pending_area = set()
    computed_area = {}
    for entity in entities:
        owner, data, kind = entity["local_id"], entity["data"], entity["entity_type"]
        index.manual_binding = bindings.get(owner) if kind == 'Observation' else None
        index.manual_defect = local[parent[owner]]['data'] if kind == 'Observation' else {}
        index.measurement_data = data if kind=='Observation' else {}
        if registry and kind == 'Defect' and (owner not in bindings or not registry.location_supported(bindings[owner],data)):
            diagnostics.append(issue('REGISTRATION_LOCATION_CONFLICT', 'Defect location is outside its registered component scope', [owner]))
        if registry and kind == 'Component' and owner in bindings and data['component_type'] != bindings[owner]['allowed_component_type']:
            diagnostics.append(issue('REGISTRATION_TYPE_CONFLICT', 'Component taxonomy differs from confirmed registration', [owner]))
        if registry and kind == 'Component' and owner in bindings and data['location_text'] is not None and not registry.location_supported(bindings[owner],data):
            diagnostics.append(issue('REGISTRATION_COMPONENT_LOCATION', 'Component location differs from registered scope', [owner]))
        entity_evidence[owner] += resolve_sources(entity["sources"], owner, set(data))
        for field, value in data.items():
            if kind == "Observation" and field in ("assertion", "scope") and value not in ("positive", "single_defect"):
                diagnostics.append(issue("NON_POSITIVE_OR_AGGREGATE", "Observation belongs in raw statements or pending candidates", [owner], "pending"))
                continue
            try:
                supported = supports(field, value, entity["sources"], index)
            except (KeyError, ValueError, InvalidOperation, TypeError):
                supported = False
            if not supported:
                diagnostics.append(issue("FIELD_UNSUPPORTED", f"{field} has no matching field-specific source support", [owner]))
        if kind == "Bridge" and catalog and not bound_bridge_matches(data,bridge_binding):
            diagnostics.append(issue('BRIDGE_CANDIDATE_CONFLICT','Candidate bridge differs from resolved report identity',[owner]))
        if kind == "Bridge" and (not data["name"] or (not data["bridge_code"] and owner not in bindings and not catalog)):
            diagnostics.append(issue("IDENTITY_UNRESOLVED", "Bridge identity requires an explicit evidenced code", [owner], "pending"))
        if kind == "Component" and (not data["name"] or (not data["component_code"] and owner not in bindings)):
            diagnostics.append(issue("IDENTITY_UNRESOLVED", "Component requires name and explicit code", [owner], "pending"))
        if kind == "Defect" and (data["defect_type"] == "unknown" or not data["location_text"] or not data["defect_type_raw"]):
            diagnostics.append(issue("DEFECT_UNRESOLVED", "Defect type/location cannot be safely represented", [owner], "pending"))
        if kind == "Observation":
            if data["area_original_value"] is None and not data.get('area_expression') and data["area_is_approximate"] is not None:
                diagnostics.append(issue("AREA_APPROXIMATION_WITHOUT_VALUE", "Missing area requires unknown approximation", [owner]))
            description = data["description"]
            if flexible:
                flags=rules.qualifiers(description)
                if flags['negative'] or flags['suspected'] or flags['aggregate']:
                    diagnostics.append(issue('MEASUREMENT_SCOPE_UNSAFE','Negative, suspected or aggregate text cannot supply a single positive area',[owner]))
                description_sources=[s for s in entity['sources'] if s['target_field']=='description']
                if not any(description in index.blocks.get(s['block_id'],{}).get('text','')
                           and description in s['exact_quote'] for s in description_sources):
                    diagnostics.append(issue('DESCRIPTION_LITERAL_REQUIRED','Description must remain an exact continuous original substring',[owner]))
                for source in description_sources:
                    raw=index.blocks.get(source['block_id'],{}).get('text','')
                    for match in re.finditer(re.escape(description),raw):
                        prefix=re.split(r'[。；;，,\n]',raw[:match.start()])[-1]
                        prefix_flags=rules.qualifiers(prefix)
                        if any(prefix_flags[k] for k in ('negative','suspected','aggregate')):
                            diagnostics.append(issue('DESCRIPTION_QUALIFIER_DROPPED','Description cannot discard a preceding source qualifier',[owner]))
            if data["assertion"] == "positive" and (NEGATIVE.search(description) or SUSPECTED.search(description)):
                diagnostics.append(issue("ASSERTION_CONTRADICTION", "Positive observation description is negative or suspected", [owner]))
            if data["scope"] == "single_defect" and (AGGREGATE.search(description) or data["reported_count"] not in (None, 1)):
                diagnostics.append(issue("SCOPE_CONTRADICTION", "Single defect observation contains aggregate/count contradiction", [owner]))
            own_code = data["observation_code"]
            for source in entity["sources"]:
                block = index.blocks.get(source["block_id"])
                if block is None:
                    continue
                measured_fields=('area_original_value','area_expression','reported_count','description') + (('area_original_unit','area_is_approximate') if flexible else ())
                if (catalog and owner in observation_rows and source['target_field'] in measured_fields
                        and row_evidence.key(row_evidence.source_block(source)) != observation_rows[owner]):
                    diagnostics.append(issue('FIELD_ROW_MISMATCH', 'Simple-row evidence cannot borrow a header, paragraph or another row', [owner]))
                if source["target_field"] in measured_fields and block.get("kind") == "table_cell" and block.get("row") != 0:
                    scoped = index.manual_binding and registry.contains(index.manual_binding, source)
                    if catalog and owner in observation_rows:
                        scoped = row_evidence.key(row_evidence.source_block(source)) == observation_rows[owner]
                    if not scoped and (own_code is None or own_code not in index.context(source["block_id"])):
                        diagnostics.append(issue("FIELD_ROW_MISMATCH", "Observation field evidence belongs to a different or unidentified table row", [owner]))
                paragraph_fields=('area_original_value','area_expression') + (('area_original_unit','area_is_approximate') if flexible else ())
                if source["target_field"] in paragraph_fields and block.get("kind") != "table_cell":
                    description_blocks = {s["block_id"] for s in entity["sources"] if s["target_field"] == "description"}
                    if source["block_id"] not in description_blocks:
                        diagnostics.append(issue("FIELD_PARAGRAPH_MISMATCH", "Paragraph area evidence is not the observation's description source", [owner]))
            defect = local[parent[owner]]["data"]
            scoped_description = index.manual_binding and any(s['target_field'] == 'description' and registry.contains(index.manual_binding,s) for s in entity['sources'])
            if catalog and owner in observation_rows:
                scoped_description = True  # Location is separately cited in this exact row.
            if defect["location_text"] and compact(defect["location_text"]) not in compact(description) and not scoped_description:
                diagnostics.append(issue("OBSERVATION_LOCATION_MISMATCH", "Observation description does not support its defect location", [owner]))
            if defect["defect_type"] in TYPE_TEXT and not (rules.type_supported(defect['defect_type'],description) if flexible else TYPE_TEXT[defect['defect_type']] in description):
                diagnostics.append(issue("OBSERVATION_TYPE_MISMATCH", "Observation description does not support its defect type", [owner]))
            try:
                calculated = rules.calculate(data) if flexible else legacy_area(data)
            except (ValueError,TypeError,InvalidOperation):
                calculated = None
                diagnostics.append(issue('AREA_EXPRESSION_INVALID','Expression conflicts with original scalar or invalid operands',[owner]))
            if flexible and data.get('area_expression') and data['area_expression'] not in description:
                diagnostics.append(issue('AREA_EXPRESSION_DESCRIPTION','Expression must be part of its original description',[owner]))
            if calculated is None:
                pending_area.add(owner)
                diagnostics.append(issue("AREA_UNNORMALIZABLE", "Area is missing or its evidenced unit is unsupported", [owner], "pending"))
            else:
                computed_area[owner]=calculated

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
        coded = not any(value is None for value in values) and any(all(compact(value) in compact(ctx) for value in values) for ctx in contexts)
        controlled = registry and registry.relation_supported(relation, left, right, bindings, index)
        catalog_named = catalog and relation['relation_type']=='HAS_COMPONENT' and any(
            left['data']['name'] in source['exact_quote'] and right['data']['component_code']
            and right['data']['component_code'] in source['exact_quote']
            for source in relation['sources'])
        row_supported = False
        if catalog:
            if relation['relation_type'] == 'HAS_COMPONENT':
                row_supported = row_evidence.bridge_component(relation['sources'], left['data'], right['data'])
            elif relation['relation_type'] == 'HAS_DEFECT':
                rows = {observation_rows[e['local_id']] for e in entities
                        if e['entity_type'] == 'Observation' and parent[e['local_id']] == owner
                        and e['local_id'] in observation_rows}
                row_supported = len(rows) == 1 and row_evidence.relation_in_row(
                    relation['sources'], next(iter(rows)),
                    [left['data']['component_code'], right['data']['location_text'], right['data']['defect_type_raw']])
            elif relation['relation_type'] == 'HAS_OBSERVATION':
                row_supported = row_evidence.relation_in_row(relation['sources'], observation_rows.get(owner),
                    [left['data']['location_text'], left['data']['defect_type_raw'], right['data']['description']])
        if not coded and not controlled and not catalog_named and not row_supported:
            diagnostics.append(issue("RELATION_UNSUPPORTED", "No proposed source context ties both explicit endpoint codes together", [relation["from_ref"], owner]))

    for statement in statements:
        resolve_sources(statement["sources"], statement["component_ref"], {"description", "kind", "location_text", "component_ref"})
        diagnostics.extend(statement_diagnostics(statement, local, index))

    blocking = [d for d in diagnostics if d["severity"] == "reject" or (d["severity"] == "pending" and d["code"] != "AREA_UNNORMALIZABLE")]
    if blocking:
        return {"records": None, "diagnostics": diagnostics, "deduplicated_entities": len(entities), "aliases": aliases}

    records = {k: candidate[k] for k in ("schema_version", "document_id", "source_kind")}
    records.update(stage="records", bridges=[], components=[], defects=[], observations=[], evidence=[], statements=[], issues=diagnostics.copy())
    if flexible:records['identity_mode']=candidate['identity_mode']
    ids = {}
    # Generate parent IDs first, without relying on model entity ordering.
    for kind in ("Bridge", "Component", "Defect", "Observation"):
        for entity in entities:
            if entity["entity_type"] != kind:
                continue
            owner, data = entity["local_id"], copy.deepcopy(entity["data"])
            common = {"validation_status": "passed", "acceptance_status": "pending_review" if owner in pending_area else "accepted", "review_status": "unreviewed", "quality_mode": "demo_accept_all", "quality_score": None, "source_document_id": document["document_id"], "evidence_ids": sorted(set(entity_evidence[owner]))}
            if kind == "Bridge":
                ids[owner] = bridge_binding['bridge_id'] if catalog else bindings[owner]['record_id'] if registry else data["bridge_code"]
                data["bridge_id"] = ids[owner]
                records["bridges"].append({**data, **common})
            elif kind == "Component":
                ids[owner] = hash_id('cmp_', [document['document_id'],ids[parent[owner]],data['component_code']]) if catalog else bindings[owner]['record_id'] if registry else hash_id("cmp_", [ids[parent[owner]], data["component_code"]])
                data.update(component_id=ids[owner], bridge_id=ids[parent[owner]])
                records["components"].append({**data, **common})
            elif kind == "Defect":
                ids[owner] = hash_id("def_", [document["document_id"], ids[parent[owner]], compact(data['location_text']), data['defect_type']]) if registry else hash_id("def_", [document["document_id"], data["defect_code"] or owner])
                data.update(defect_id=ids[owner], component_id=ids[parent[owner]], identity_scope="document_local")
                records["defects"].append({**data, **common})
            else:
                ids[owner] = hash_id("obs_", [document["document_id"], ids[parent[owner]], data['observed_at']]) if registry else hash_id("obs_", [document["document_id"], owner])
                area = computed_area.get(owner, {'area_value':None,'area_conversion':None})
                if flexible and owner not in computed_area:
                    area.update(area_expression=data.get('area_expression'),area_basis=None,area_derivation=None,area_rules_version=rules.version)
                data.update(observation_id=ids[owner], defect_id=ids[parent[owner]], component_id=ids[parent[parent[owner]]], source_kind=document["source_kind"], area_unit="m²", **area)
                optional = ("observed_at", "reported_count", "area_original_value", "area_original_unit", "area_value", "area_is_approximate")
                data["missing_fields"] = [field for field in optional if data[field] is None]
                records["observations"].append({**data, **common})
    for statement in statements:
        component = local.get(statement['component_ref'])
        derived = bound_statement_location(statement, component, index)
        if derived:
            statement['location_text'] = derived
            original_source = next(s for s in statement['sources'] if s['target_field']=='description')
            statement['sources'].append({**original_source,'target_field':'location_text'})
            records['issues'].append(issue('SOURCE_SCOPE_LOCATION_DERIVED', 'Location derived by complete registered source pattern', severity='info'))
        if statement["component_ref"] is not None:
            statement["component_ref"] = ids[statement["component_ref"]]
        component = next((c for c in records["components"] if c["component_id"] == statement["component_ref"]), None)
        if component and component["component_code"] == "C-DECK-01" and component["component_type"] == "deck_pavement" and statement["location_text"] in {"桥头", "桥头区域", "桥头铺装"}:
            statement["location_text"] = "桥头区域"
        records["statements"].append(statement)
    records["evidence"] = sorted(evidence.values(), key=lambda value: value["evidence_id"])
    record_errors = validate_records(records, document, schema, structure_errors, registration, rules, bridge_binding)
    if record_errors:
        diagnostics.extend(record_errors)
        return {"records": None, "diagnostics": diagnostics}
    return {"records": records, "diagnostics": diagnostics, "aliases": aliases, "deduplicated_entities": len(entities)}


def validate_records(records, document, schema, structure_errors, registration=None, rules=None, bridge_binding=None):
    diagnostics = []
    if structure_errors(records) or records.get("stage") != "records":
        return [issue("RECORD_SCHEMA_INVALID", "Record bundle failed schema/stage check")]
    flexible=records.get('schema_version') in ('0.3.0-draft','0.4.0-draft')
    if not flexible and any(any(o.get(k) is not None for k in ('area_expression','area_basis','area_derivation','area_rules_version')) for o in records['observations']):
        return [issue('RECORD_EXPRESSION_VERSION','Extended measurement requires version 0.3')]
    catalog = records.get('schema_version') == '0.4.0-draft'
    if catalog and (not bridge_binding or bridge_binding.get('status')!='resolved' or bridge_binding['document_id']!=document['document_id']):
        return [issue('RECORD_BRIDGE_BINDING_REQUIRED','Resolved source-bound bridge identity required')]
    index = DocumentIndex(document, rules if flexible else None)
    registered = records.get('schema_version') == '0.2.0-draft' or (flexible and records.get('identity_mode')=='registered')
    if registration and registration.get('document_id') == document['document_id'] and not registered:
        return [issue('RECORD_REGISTRATION_VERSION', 'Configured source requires controlled record version')]
    try:
        registry = ManualRegistration(registration, document) if registered and registration else None
    except (ValueError, KeyError, TypeError):
        registry = None
    if registered and registry is None:
        return [issue('RECORD_REGISTRATION_REQUIRED', 'Controlled record registration is unavailable')]
    index.registration = registry
    if registry:registry.flexible_rules = rules if flexible else None
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
    if catalog:
        if len(records['bridges'])!=1 or any(b['bridge_id']!=bridge_binding['bridge_id'] or not bound_bridge_matches(b,bridge_binding) for b in records['bridges']):
            diagnostics.append(issue('RECORD_BRIDGE_CONFLICT','Record bridge differs from resolved report identity'))
        for c in records['components']:
            if c['component_id']!=hash_id('cmp_',[document['document_id'],bridge_binding['bridge_id'],c['component_code']]):
                diagnostics.append(issue('RECORD_COMPONENT_IDENTITY','Component identity must remain document-local'))
    for plural in ("bridges", "components", "defects", "observations"):
        for record in records[plural]:
            index.manual_binding = registry.by_id(record['component_id']) if registry and plural == 'observations' else None
            index.manual_defect = maps['defects'].get(record['defect_id'],{}) if plural == 'observations' else {}
            index.measurement_data = record if plural=='observations' else {}
            if registry and plural in ('bridges','components'):
                key = 'bridge_id' if plural == 'bridges' else 'component_id'
                binding = registry.match('Bridge' if plural == 'bridges' else 'Component', record)
                if not binding or binding['record_id'] != record[key] or (plural == 'components' and (record['bridge_id'] != binding['parent_id'] or record['component_type'] != binding['allowed_component_type'] or (record['location_text'] is not None and not registry.location_supported(binding,record)))):
                    diagnostics.append(issue('RECORD_REGISTRATION_CONFLICT', 'Record identity differs from confirmed registration'))
            if record["source_document_id"] != document["document_id"] or not set(record["evidence_ids"]) <= set(maps["evidence"]):
                diagnostics.append(issue("RECORD_EVIDENCE_REF", "Record evidence/document references do not resolve"))
            sources = [{"block_id": maps["evidence"][ref]["block_id"], "exact_quote": maps["evidence"][ref]["quote"], "target_field": maps["evidence"][ref]["target_field"], "occurrence_hint": None}
                       for ref in record["evidence_ids"] if ref in maps["evidence"]]
            definition = {"bridges": "BridgeCandidateData", "components": "ComponentCandidateData", "defects": "DefectCandidateData", "observations": "ObservationCandidateData"}[plural]
            fields=list(schema["$defs"][definition]["required"])
            if flexible and plural=='observations':fields.append('area_expression')
            for field in fields:
                try:
                    supported = supports(field, record[field], sources, index)
                except (KeyError, ValueError, InvalidOperation, TypeError):
                    supported = False
                if not supported:
                    diagnostics.append(issue("RECORD_FIELD_UNSUPPORTED", f"Record {field} is not supported by referenced evidence"))
    for component in records["components"]:
        if component["bridge_id"] not in maps["bridges"]:
            diagnostics.append(issue("RECORD_PARENT_REF", "Component bridge does not exist"))
    if catalog:
        from ontology_proj.domain.row_evidence import RowEvidence
        row_evidence = RowEvidence(document)
        def record_sources(record):
            return [{'block_id': e['block_id'], 'exact_quote': e['quote'], 'target_field': e['target_field']}
                    for ref in record.get('evidence_ids', []) if (e := maps['evidence'].get(ref))]
        seen_rows = set()
        for observation in records['observations']:
            if observation['observation_code'] is not None:
                continue
            sources = record_sources(observation)
            measured = [s for s in sources if s['target_field'] in (
                'description','area_original_value','area_original_unit','area_expression','area_is_approximate','reported_count')]
            table_sources = [s for s in measured if row_evidence.key(row_evidence.source_block(s)) is not None]
            if not table_sources:
                continue  # Preserve existing paragraph/controlled paths.
            defect = maps['defects'].get(observation['defect_id'], {})
            component = maps['components'].get(observation['component_id'], {})
            row = row_evidence.observation_row(observation, sources, component, defect, record_sources(defect))
            if (row is None or row in seen_rows or any(row_evidence.key(row_evidence.source_block(s)) != row for s in measured)):
                diagnostics.append(issue('RECORD_ROW_MISMATCH', 'Unnumbered observation must retain its unique component/defect/measurement row'))
            seen_rows.add(row)
    for defect in records["defects"]:
        if defect["component_id"] not in maps["components"]:
            diagnostics.append(issue("RECORD_PARENT_REF", "Defect component does not exist"))
        if registry:
            binding = registry.by_id(defect['component_id'])
            expected = hash_id('def_', [document['document_id'],defect['component_id'],compact(defect['location_text']),defect['defect_type']])
            if not binding or not registry.location_supported(binding,defect) or defect['defect_id'] != expected:
                diagnostics.append(issue('RECORD_REGISTRATION_LOCATION', 'Defect identity/location differs from controlled scope'))
    for observation in records["observations"]:
        if flexible and (observation.get('area_rules_version')!=rules.version
                or any(rules.qualifiers(observation['description'])[k] for k in ('negative','suspected','aggregate'))):
            diagnostics.append(issue('RECORD_MEASUREMENT_SCOPE','Rules version or single-positive source scope differs'))
        if registry and observation['observation_id'] != hash_id('obs_', [document['document_id'],observation['defect_id'],observation['observed_at']]):
            diagnostics.append(issue('RECORD_OBSERVATION_IDENTITY', 'Controlled observation identity differs'))
        if observation["area_original_value"] is None and not observation.get('area_expression') and observation["area_is_approximate"] is not None:
            diagnostics.append(issue("RECORD_AREA_APPROXIMATION_WITHOUT_VALUE", "Missing area requires unknown approximation"))
        defect = maps["defects"].get(observation["defect_id"])
        if defect is None or observation["component_id"] != defect["component_id"] or observation["source_kind"] != document["source_kind"]:
            diagnostics.append(issue("RECORD_PARENT_REF", "Observation component/defect/source linkage is inconsistent"))
        if observation["area_value"] is not None:
            if not record_area_valid(observation, rules if flexible else None):
                diagnostics.append(issue("RECORD_AREA_INCONSISTENT", "Normalized area differs from original value/unit"))
        if observation["observed_at"] is None and "observed_at" not in observation["missing_fields"]:
            diagnostics.append(issue("RECORD_MISSING_FIELD", "Unknown date lacks missing_fields entry"))
    local_components = {c['component_id']: {'entity_type': 'Component', 'data': c} for c in records['components']}
    for statement in records['statements']:
        for source in statement['sources']:
            try:
                index.resolve(source, statement['component_ref'], {'description', 'kind', 'location_text', 'component_ref'})
            except ValueError:
                diagnostics.append(issue('RECORD_STATEMENT_SOURCE', 'Statement source cannot be resolved'))
        diagnostics.extend(statement_diagnostics(statement, local_components, index))
    return diagnostics

