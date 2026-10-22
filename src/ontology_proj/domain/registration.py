"""Explicit operator registration, confined to one source document and spans."""
import re
from decimal import Decimal


def compact(value):
    return re.sub(r'\s+', '', value)


class ManualRegistration:
    def __init__(self, value, document):
        if (value.get('status') != 'confirmed_by_user' or value.get('version') != 'manual-registration-1'
                or value.get('document_id') != document['document_id'] or document['source_kind'] != 'real'):
            raise ValueError('Unconfirmed or mismatched registration')
        self.document = document
        self.blocks = {b['block_id']: b for b in document['blocks']}
        self.entries = value['entries']
        if len({e['record_id'] for e in self.entries}) != len(self.entries):
            raise ValueError('Duplicate registration ID')
        bridges = [e for e in self.entries if e['kind'] == 'Bridge']
        if len(bridges) != 1 or document['import_context']['bridge_id'] != bridges[0]['record_id']:
            raise ValueError('Trusted bridge context differs from registration')
        self.bridge = bridges[0]
        for entry in self.entries:
            if entry['kind'] not in ('Bridge', 'Component') or entry['original_code'] is not None:
                raise ValueError('Only missing-code bridge/component registration is supported')
            if not entry['names'] or not entry['sources']:
                raise ValueError('Registration requires names and source evidence')
            if entry['kind'] == 'Component' and entry['parent_id'] != self.bridge['record_id']:
                raise ValueError('Registered component belongs to another bridge')
            for source in entry['sources']:
                text = self.blocks[source['block_id']]['text']
                if text.count(source['quote']) != 1 or not any(compact(n) in compact(source['quote']) for n in entry['names']):
                    raise ValueError('Registration name evidence differs from source')
            for scope in entry.get('observation_scopes', []) + entry.get('statement_scopes', []):
                text = self.blocks[scope['block_id']]['text'];start,end = scope['charspan']
                if not 0 <= start < end <= len(text) or text[start:end] != scope['quote']:
                    raise ValueError('Registered observation span differs from source')
        pairs = [(e['kind'], compact(n)) for e in self.entries for n in e['names']]
        if len(set(pairs)) != len(pairs):
            raise ValueError('Ambiguous registration names')

    def match(self, kind, data):
        if kind not in ('Bridge', 'Component'):
            return None
        code = data['bridge_code' if kind == 'Bridge' else 'component_code']
        matches = [e for e in self.entries if e['kind'] == kind and code == e['original_code']
                   and compact(data['name'] or '') in {compact(n) for n in e['names']}]
        return matches[0] if len(matches) == 1 else None

    def by_id(self, value):
        return next((e for e in self.entries if e['record_id'] == value), None)

    def contains(self, binding, source, kind='observation_scopes'):
        block = self.blocks.get(source['block_id'])
        if block is None or not source['exact_quote']:
            return False
        starts = [m.start() for m in re.finditer(re.escape(source['exact_quote']), block['text'])]
        hint = source.get('occurrence_hint')
        if hint is None:
            if len(starts) != 1: return False
            start = starts[0]
        elif type(hint) is int and 0 <= hint < len(starts): start = starts[hint]
        else: return False
        end = start + len(source['exact_quote'])
        return any(s['block_id'] == source['block_id'] and s['charspan'][0] <= start < end <= s['charspan'][1]
                   for s in binding.get(kind, []))

    def location_supported(self, binding, data):
        return compact(data.get('location_text') or '') in {compact(s) for s in binding.get('defect_location_markers',[])}

    def scoped_support(self, field, value, sources, binding, defect):
        """None defers to ordinary support; False always denies out-of-scope quotes."""
        if field not in ('description','assertion','scope','reported_count','observed_at','area_original_value',
                         'area_original_unit','area_is_approximate','area_expression') or value is None:
            return None
        matching = [s for s in sources if s['target_field'] == field]
        if not matching or not all(self.contains(binding,s) for s in matching):
            return False
        if getattr(self,'flexible_rules',None) is not None:
            return None
        quotes = [s['exact_quote'] for s in matching]
        if field == 'area_original_value':
            numbers = [n for q in quotes for n in re.findall(
                r'(?:面积\s*(?:为|约|=|：|:)\s*|S\s*=\s*)(\d+(?:\.\d+)?)(?=$|[^\w.]|m(?:²|\s*2)(?![\w.]))',q)]
            return any(Decimal(n) == Decimal(value) for n in numbers)
        if field in ('assertion','scope'):
            word = {'pothole':'坑槽','exposed_rebar':'露筋'}.get(defect.get('defect_type'))
            safe = bool(word) and any(word in q and not re.search(r'未见|未发现|无明显|没有|疑似|可能|全桥|合计|累计|多处|若干',q) for q in quotes)
            return safe and value == ('positive' if field == 'assertion' else 'single_defect')
        return None

    def relation_supported(self, relation, left, right, bindings, index):
        sources = relation['sources']
        if not sources or any(s['block_id'] not in self.blocks for s in sources): return False
        lb = bindings.get(left['local_id']);rb = bindings.get(right['local_id'])
        quotes = [s['exact_quote'] for s in sources]
        if relation['relation_type'] == 'HAS_COMPONENT':
            return bool(lb and rb and rb['parent_id'] == lb['record_id'] and
                        any(any(compact(n) in compact(q) for n in rb['names']) for q in quotes))
        if relation['relation_type'] == 'HAS_DEFECT':
            location = compact(right['data']['location_text'] or '')
            named = lb and any(any(compact(n) in compact(q) for n in lb['names']) for q in quotes)
            scoped = lb and any(self.contains(lb,s) for s in sources)
            return bool(lb and location and self.location_supported(lb,right['data'])
                        and any(location in compact(q) for q in quotes) and (named or scoped))
        if relation['relation_type'] == 'HAS_OBSERVATION':
            binding = bindings.get(right['local_id'])
            description = right['data']['description']
            return bool(binding and any(self.contains(binding,s) and description in s['exact_quote'] for s in sources))
        return False
