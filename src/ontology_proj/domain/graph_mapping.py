"""Fixed scalar projection of validated records. No model-generated graph code."""
import json
LABELS = {
    'bridges': ('Bridge', 'bridge_id', ('bridge_code', 'name')),
    'components': ('Component', 'component_id', ('component_code', 'name', 'component_type', 'location_text', 'bridge_id')),
    'defects': ('Defect', 'defect_id', ('defect_code', 'defect_type', 'defect_type_raw', 'location_text', 'component_id', 'identity_scope')),
    'observations': ('Observation', 'observation_id', ('observation_code', 'observed_at', 'assertion', 'scope', 'reported_count', 'description', 'defect_id', 'component_id', 'source_kind', 'area_original_value', 'area_original_unit', 'area_value', 'area_unit', 'area_conversion', 'area_is_approximate', 'missing_fields', 'area_expression','area_basis','area_derivation_json','area_rules_version')),
}
COMMON = ('validation_status', 'acceptance_status', 'review_status', 'quality_mode', 'quality_score', 'source_document_id')
EVIDENCE = ('evidence_id', 'document_id', 'block_id', 'quote', 'target_field', 'page_no', 'bbox', 'charspan', 'location_level', 'source_ref')


def scalar_properties(value, fields):
    result = {k: value[k] for k in fields if k in value}
    for v in result.values():
        if v is None or type(v) in (str, bool, int, float):
            continue
        if isinstance(v, list) and (not v or len({type(x) for x in v}) == 1) and all(type(x) in (str, bool, int, float) for x in v):
            continue
        raise ValueError('Non-scalar graph property')
    return result


def project_bundle(document, records):
    if records['document_id'] != document['document_id'] or records['source_kind'] != document['source_kind']:
        raise ValueError('Document identity mismatch')
    count = sum(len(records[k]) for k in LABELS)
    if count > 500:
        raise ValueError('Candidate limit exceeded')
    nodes = {}
    for plural, (label, identity, fields) in LABELS.items():
        nodes[label] = []
        for r in records[plural]:
            value=dict(r)
            if 'area_derivation' in r:
                value['area_derivation_json']=json.dumps(r['area_derivation'],ensure_ascii=False,sort_keys=True,separators=(',',':'))
            extra=()
            if label=='Bridge' and records.get('identity_mode')=='catalog':
                binding=document.get('bridge_binding')
                if not binding or binding['bridge_id']!=r[identity]:raise ValueError('Bridge binding required for projection')
                value.update(name=binding['bridge']['name'],bridge_code=None,location=binding['bridge']['location'],identity_version=binding['version'])
                extra=('location','identity_version')
            nodes[label].append(dict(id=r[identity],properties=scalar_properties(value,(identity,*fields,*COMMON,*extra)),evidence_ids=r['evidence_ids']))
    blocks = [dict(block_id=b['block_id'], document_id=document['document_id'], text=b['text'], kind=b['kind'], page_no=b['locations'][0]['page_no'] if b['locations'] else None) for b in document['blocks']]
    evidence = [scalar_properties(e, EVIDENCE) for e in records['evidence']]
    return {'nodes': nodes, 'blocks': blocks, 'evidence': evidence}


def decode_measurement(record):
    result=dict(record)
    if result.get('area_derivation_json') is not None:
        result['area_derivation']=json.loads(result.pop('area_derivation_json'))
        if result['area_derivation'] is not None and not isinstance(result['area_derivation'],dict):raise ValueError('Invalid derivation')
    return result
