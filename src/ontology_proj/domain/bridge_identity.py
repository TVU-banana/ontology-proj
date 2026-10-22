"""Conservative bridge identity decisions, independent of IO and model output."""
import hashlib
import json
import re
import unicodedata

VERSION = 'bridge-identity-1'


def normalized(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value or '')).casefold()


def official_key(value):
    # Official codes are authority-defined; do not assume case or internal spacing equivalence.
    return unicodedata.normalize('NFKC', value or '').strip()


def snapshot_hash(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def source_facts(document):
    labels = {'name': '桥梁名称|桥名', 'location': '地理位置|桥梁位置|桥址|所在位置',
              'official_code': '官方编号|桥梁编号|桥梁编码', 'official_namespace': '编号体系|编号命名空间'}
    values = {k: [] for k in labels}
    for block in document['blocks']:
        for key, label in labels.items():
            for m in re.finditer(r'(?:^|\n)\s*(?:' + label + r')\s*[:：]\s*([^\n；;]+)', block['text']):
                value = m.group(1).strip()
                if value:
                    values[key].append({'value': value, 'block_id': block['block_id'], 'quote': m.group(0), 'charspan': [m.start(),m.end()]})
    # Explicit adjacent label/value cells; do not infer merged or displaced rows.
    cells=[b for b in document['blocks'] if b.get('kind')=='table_cell']
    for label_cell in cells:
        label_text=label_cell['text'].strip().rstrip('：:').strip()
        key=next((k for k,pattern in labels.items() if re.fullmatch(pattern,label_text)),None)
        if not key or label_cell.get('row_span')!=1:continue
        adjacent=[b for b in cells if b.get('table_id')==label_cell.get('table_id') and b.get('row')==label_cell.get('row')
                  and b.get('row_span')==1 and b.get('column')==label_cell.get('column',-1)+label_cell.get('column_span',1)]
        if len(adjacent)==1 and adjacent[0]['text'].strip():
            block=adjacent[0];value=block['text'].strip()
            values[key].append({'value':value,'block_id':block['block_id'],'quote':block['text'],'charspan':[0,len(block['text'])],
                                'label_source':{'block_id':label_cell['block_id'],'quote':label_cell['text']}})
    distinct = {k: sorted({(official_key if k.startswith('official_') else normalized)(x['value']) for x in entries}) for k, entries in values.items()}
    return {'values': {k: entries[0]['value'] if len(distinct[k]) == 1 else None for k, entries in values.items()},
            'evidence': values, 'ambiguous': any(len(v)>1 for v in distinct.values())}


def decision(bridges, facts, source_kind, selected=None, confirm_details=False):
    """A resolved ID is returned only when every supplied strong signal agrees."""
    def pending(code, candidates=()):
        return {'status':'needs_confirmation','reason':code,'bridge_id':None,'candidates':sorted(candidates)}
    if facts['ambiguous']:return pending('BRIDGE_SOURCE_AMBIGUOUS')
    v=facts['values'];pool=[b for b in bridges if b['source_kind']==source_kind]
    code,namespace=v['official_code'],v['official_namespace']
    official=[b for b in pool if code and namespace and any(official_key(c['code'])==official_key(code) and official_key(c['namespace'])==official_key(namespace) for c in b['official_ids'])]
    named=[b for b in pool if v['name'] and v['location'] and normalized(b['name'])==normalized(v['name']) and normalized(b['location'])==normalized(v['location'])]
    if len(official)>1 or len(named)>1 and not selected:return pending('BRIDGE_MATCH_AMBIGUOUS',[b['bridge_id'] for b in official+named])
    chosen=next((b for b in pool if b['bridge_id']==selected),None) if selected else None
    if selected and chosen is None:return pending('BRIDGE_SELECTION_UNKNOWN')
    if chosen and (official and official[0]['bridge_id']!=chosen['bridge_id'] or named and chosen['bridge_id'] not in {b['bridge_id'] for b in named}):return pending('BRIDGE_SELECTION_CONFLICT')
    if official and named and official[0]['bridge_id'] not in {b['bridge_id'] for b in named}:return pending('BRIDGE_SIGNALS_CONFLICT')
    target=chosen or (official[0] if official else named[0] if named else None)
    if target is None:return pending('BRIDGE_MATCH_MISSING')
    # Full source name/location must agree with selected/official target when present.
    if not (chosen and confirm_details) and any(v[k] and target[k] and normalized(v[k])!=normalized(target[k]) for k in ('name','location')):return pending('BRIDGE_DETAILS_CONFLICT')
    if code:
        if target.get('legacy_code') and official_key(target['legacy_code'])!=official_key(code):return pending('BRIDGE_OFFICIAL_CONFLICT')
        if not namespace:
            same=[c for c in target['official_ids'] if official_key(c['code'])==official_key(code)]
            if chosen and (len(same)==1 or target.get('legacy_code') and official_key(target['legacy_code'])==official_key(code)):
                return {'status':'resolved','reason':'user_selection','bridge_id':target['bridge_id'],'candidates':[target['bridge_id']]}
            return pending('BRIDGE_CODE_NAMESPACE_REQUIRED')
        existing=[c for c in target['official_ids'] if official_key(c['namespace'])==official_key(namespace)]
        if existing and any(official_key(c['code'])!=official_key(code) for c in existing):return pending('BRIDGE_OFFICIAL_CONFLICT')
    return {'status':'resolved','reason':'user_confirmation' if chosen and confirm_details else 'user_selection' if chosen else 'official_id' if official else 'name_location',
            'bridge_id':target['bridge_id'],'candidates':[target['bridge_id']]}


def bound_bridge_matches(data, binding):
    facts=binding['facts']['values'];bridge=binding['bridge']
    return ((bool(data.get('name')) if binding.get('confirmed_details') and facts['name'] is None else normalized(data.get('name'))==normalized(facts['name'] or bridge['name']))
            and (data.get('bridge_code') is None if facts['official_code'] is None
                 else official_key(data.get('bridge_code'))==official_key(facts['official_code'])))
