"""Bounded exact lookup, with Decimal and explicit scope semantics."""
from datetime import date
from decimal import Decimal, InvalidOperation
from ontology_proj.application.files import FileStoreError
from ontology_proj.domain.graph_mapping import LABELS, COMMON, decode_measurement
from ontology_proj.domain.measurement import record_area_valid


TYPES = {'bridge_components', 'component_observations', 'defect_history', 'record_by_id'}


def invalid(message='Query fields are invalid'):
    raise FileStoreError('INVALID_REQUEST', message)


def number(value):
    if not isinstance(value, str) or len(value) > 128:
        invalid('Area bounds must be decimal strings')
    try:
        n = Decimal(value)
    except InvalidOperation:
        invalid('Area is not a decimal')
    if not n.is_finite() or n < 0:
        invalid('Area must be finite and nonnegative')
    return n


def day(value):
    if not isinstance(value, str) or len(value) != 10:
        invalid('Date must be YYYY-MM-DD')
    try:
        d = date.fromisoformat(value)
    except ValueError:
        invalid('Date must be YYYY-MM-DD')
    if d.isoformat() != value:
        invalid('Date must be YYYY-MM-DD')
    return d


def validate(request):
    if not isinstance(request, dict) or set(request) - {'query_type','scope','record_id','defect_id','acceptance_mode','area_min','area_max'}:
        invalid()
    q = dict(request)
    if not isinstance(q.get('query_type'),str) or q['query_type'] not in TYPES:
        invalid('Unknown query type')
    s = q.get('scope')
    if not isinstance(s, dict) or set(s) - {'bridge_id','component_ids','document_ids','date_from','date_to','review_mode','include_synthetic'}:
        invalid('Scope fields are invalid')
    s = dict(s)
    for key in ('component_ids','document_ids'):
        v=s.setdefault(key, [])
        if not isinstance(v,list) or len(v)>100 or any(not isinstance(i,str) or not i or len(i)>256 for i in v):invalid()
        s[key]=list(dict.fromkeys(v))
    for key in ('bridge_id','date_from','date_to'):
        s.setdefault(key,None)
    if s['bridge_id'] is not None and (not isinstance(s['bridge_id'],str) or not s['bridge_id'] or len(s['bridge_id'])>256):invalid()
    if not s['document_ids'] and not s['bridge_id']:invalid('Select documents or a bridge')
    s.setdefault('review_mode','accepted');s.setdefault('include_synthetic',True)
    if not isinstance(s['review_mode'],str) or s['review_mode'] not in ('accepted','confirmed_only') or type(s['include_synthetic']) is not bool:invalid()
    q.setdefault('acceptance_mode','accepted')
    if not isinstance(q['acceptance_mode'],str) or q['acceptance_mode'] not in ('accepted','pending_review') or (q['acceptance_mode']=='pending_review' and s['review_mode']=='confirmed_only'):invalid()
    for key in ('date_from','date_to'):
        if s[key] is not None:day(s[key])
    if s['date_from'] and s['date_to'] and s['date_from']>s['date_to']:invalid('Date range is reversed')
    for key in ('area_min','area_max'):
        q.setdefault(key,None)
        if q[key] is not None:number(q[key])
    if q['area_min'] is not None and q['area_max'] is not None and number(q['area_min'])>number(q['area_max']):invalid('Area range is reversed')
    for key in ('record_id','defect_id'):
        q.setdefault(key,None)
        if q[key] is not None and (not isinstance(q[key],str) or not q[key] or len(q[key])>256):invalid()
    if q['query_type']=='record_by_id' and not q['record_id']:invalid('Record ID is required')
    if q['query_type']=='defect_history' and not q['defect_id']:invalid('Defect ID is required')
    if q['query_type']=='component_observations' and not s['component_ids']:invalid('Component IDs are required')
    if q['query_type']=='bridge_components' and (s['date_from'] or s['date_to'] or q['area_min'] is not None or q['area_max'] is not None):invalid('Date and area apply to observations')
    q['scope']=s
    return q


class LookupGraph:
    def __init__(self, repository, rules=None):self.repository,self.rules=repository,rules

    def execute(self, request):
        q=validate(request);rows=self.repository.lookup(q)
        if len(rows)>500:raise FileStoreError('RESULT_LIMIT_EXCEEDED','Please narrow the query scope')
        result=[];s=q['scope'];omitted_unknown_date_count=0
        filters=s['date_from'] or s['date_to'] or q['area_min'] is not None or q['area_max'] is not None
        for row in rows:
            try:r=decode_measurement(row)
            except (ValueError,TypeError):raise FileStoreError('GRAPH_RECORD_INVALID','Stored derivation is invalid') from None
            label=r['record_type'];definition=next(v for v in LABELS.values() if v[0]==label)
            for field in (*definition[2],*COMMON):r.setdefault(field,None)
            if label=='Observation' and r.get('area_rules_version') is not None and r.get('area_value') is not None:
                if not record_area_valid(r,self.rules):raise FileStoreError('GRAPH_RECORD_INVALID','Stored area derivation is inconsistent')
            if filters and label!='Observation':invalid('Date and area apply only to observations')
            observed=r.get('observed_at')
            if s['date_from'] or s['date_to']:
                if observed is None:
                    omitted_unknown_date_count+=1
                    continue
                try:d=day(observed)
                except FileStoreError:raise FileStoreError('GRAPH_RECORD_INVALID','Stored observation date is invalid') from None
                if s['date_from'] and d<day(s['date_from']):continue
                if s['date_to'] and d>day(s['date_to']):continue
            if q['area_min'] is not None or q['area_max'] is not None:
                if r.get('area_value') is None:continue
                if r.get('area_unit')!='m²':raise FileStoreError('GRAPH_RECORD_INVALID','Stored area unit is invalid')
                try:n=number(r['area_value'])
                except FileStoreError:raise FileStoreError('GRAPH_RECORD_INVALID','Stored area is invalid') from None
                if q['area_min'] is not None and n<number(q['area_min']):continue
                if q['area_max'] is not None and n>number(q['area_max']):continue
            result.append(r)
        result.sort(key=lambda r:(r['record_type'],r['record_id']))
        warnings=['Pending records are for inspection, not accepted business facts'] if q['acceptance_mode']=='pending_review' else []
        if omitted_unknown_date_count:
            warnings.append(f'Date filter excluded {omitted_unknown_date_count} observations with unknown dates')
        return {'records':result,'count':len(result),'warnings':warnings,'omitted_unknown_date_count':omitted_unknown_date_count}
