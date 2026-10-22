import copy
import json

import pytest

from ontology_proj.adapters.business_rules import load_rules
from ontology_proj.adapters.business_validation import BusinessValidator
from ontology_proj.domain.graph_mapping import project_bundle,decode_measurement
from ontology_proj.domain.measurement import record_area_valid


RULES=load_rules()


def example(measurement, value=None, unit=None, *, derived=False, approximate=False):
    description='主梁左侧存在一处露筋，'+measurement+'。'
    text='测试桥 B-TEST，主梁 C-TEST，病害 D-TEST，观测 O-TEST；'+description+'检测日期未知。'
    b={'block_id':'test-block','kind':'paragraph','text':text,'source_ref':'#/texts/0',
       'locations':[{'page_no':1,'bbox':[.1,.1,.9,.3],'charspan':[0,len(text)]}]}
    doc={'document_id':'doc_rules_test','source_kind':'synthetic','pages':[{'page_no':1}],'blocks':[b]}
    def entity(local,kind,data):
        return {'local_id':local,'entity_type':kind,'data':data,
                'sources':[{'block_id':b['block_id'],'exact_quote':text,'target_field':k,'occurrence_hint':None}
                           for k,v in data.items() if v is not None]}
    observation={'observation_code':'O-TEST','observed_at':None,'assertion':'positive','scope':'single_defect','reported_count':1,
        'description':description,'area_original_value':value,'area_original_unit':unit,
        'area_is_approximate':approximate,'area_expression':measurement if derived else None}
    candidate={'schema_version':'0.3.0-draft','identity_mode':'explicit','stage':'candidate','document_id':doc['document_id'],'source_kind':'synthetic',
       'entities':[entity('b','Bridge',{'bridge_code':'B-TEST','name':'测试桥'}),
                   entity('c','Component',{'component_code':'C-TEST','name':'主梁','component_type':'girder','location_text':'左侧'}),
                   entity('d','Defect',{'defect_code':'D-TEST','defect_type':'exposed_rebar','defect_type_raw':'露筋','location_text':'左侧'}),
                   entity('o','Observation',observation)],
       'relations':[{'relation_type':kind,'from_ref':left,'to_ref':right,'sources':[{'block_id':b['block_id'],'exact_quote':text,'target_field':'description','occurrence_hint':None}]}
                    for left,right,kind in [('b','c','HAS_COMPONENT'),('c','d','HAS_DEFECT'),('d','o','HAS_OBSERVATION')]],
       'statements':[],'issues':[]}
    return doc,candidate


def validated(*args,**kwargs):
    doc,candidate=example(*args,**kwargs);validator=BusinessValidator();result=validator.validate(candidate,doc)
    assert result['records'] is not None,result['diagnostics']
    assert not validator.validate_records(result['records'],doc)
    return doc,result['records']


@pytest.mark.parametrize('raw,value,unit,expected',[
    ('面积为100平方厘米','100','平方厘米','0.01'),('面积：100cm²','100','cm²','0.01'),
    ('S = 100 cm2','100','cm2','0.01'),('病害面积为0.01平方米','0.01','平方米','0.01'),
    ('缺损面积为10000平方毫米','10000','平方毫米','0.01'),('面积为10000mm²','10000','mm²','0.01'),
    ('面积为100m²','100','m²','100'),('面积为100 CM²','100','CM²','0.01')])
def test_equivalent_direct_expressions(raw,value,unit,expected):
    doc,records=validated(raw,value,unit)
    o=records['observations'][0]
    assert o['area_value']==expected and o['area_basis']=='reported'
    assert o['area_original_value']==value and o['area_original_unit']==unit
    assert o['area_expression'] is None and record_area_valid(o,RULES)


@pytest.mark.parametrize('raw,expected,basis',[
    ('矩形病害，长10厘米、宽10厘米','0.01','rectangle_dimensions'),
    ('矩形病害，宽10厘米，长0.1米','0.01','rectangle_dimensions'),
    ('矩形病害，长10厘米宽10厘米','0.01','rectangle_dimensions'),
    ('S=2.5×0.9㎡','2.25','explicit_area_product'),
    ('面积=10乘以10平方厘米','0.01','explicit_area_product'),
    ('面积=10*10cm²','0.01','explicit_area_product')])
def test_derived_areas_keep_original_expression_and_inputs(raw,expected,basis):
    _,records=validated(raw,derived=True)
    o=records['observations'][0]
    assert o['area_value']==expected and o['area_basis']==basis
    assert o['area_original_value'] is None and o['area_original_unit'] is None
    assert o['area_expression']==raw and len(o['area_derivation']['inputs'])==2
    assert 'area_value' not in o['missing_fields']


@pytest.mark.parametrize('raw',[
    '矩形病害，长10、宽10，单位未知','不规则病害，长10厘米、宽10厘米',
    '矩形病害，最大长10厘米、最大宽10厘米','S=2×3×4㎡','S=-10×10cm²',
    '尺寸10×10，单位未知','矩形病害，长10厘米、宽10厘米，面积为200平方厘米',
    '矩形病害，长10厘米至20厘米，宽10厘米','矩形病害，长10厘米、宽10毫米至20毫米',
    'S=2~3×4㎡','S=2×3㎡，另有面积为7㎡'])
def test_unsupported_dimensions_do_not_generate_area(raw):
    doc,candidate=example(raw,derived=True)
    result=BusinessValidator().validate(candidate,doc)
    assert result['records'] is None or all(o['area_value'] is None and o['acceptance_status']=='pending_review' for o in result['records']['observations'])


@pytest.mark.parametrize('term',['未见','疑似','两处总面积','全桥合计'])
def test_negation_suspicion_aggregate_not_positive_area(term):
    doc,candidate=example('面积为100平方厘米','100','平方厘米')
    text=candidate['entities'][-1]['data']['description'].replace('存在一处',term)
    original=doc['blocks'][0]['text'];doc['blocks'][0]['text']=original.replace(candidate['entities'][-1]['data']['description'],text)
    doc['blocks'][0]['locations'][0]['charspan'][1]=len(doc['blocks'][0]['text'])
    candidate['entities'][-1]['data']['description']=text
    for owner in candidate['entities']+candidate['relations']:
        for s in owner['sources']:s['exact_quote']=doc['blocks'][0]['text']
    assert BusinessValidator().validate(candidate,doc)['records'] is None


def test_approximation_preserved():
    _,records=validated('面积约100平方厘米','100','平方厘米',approximate=True)
    assert records['observations'][0]['area_is_approximate'] is True
    doc,candidate=example('面积约100平方厘米','100','平方厘米',approximate=False)
    assert BusinessValidator().validate(candidate,doc)['records'] is None


@pytest.mark.parametrize('value,unit',[('100','m²'),('0.01','cm²'),('100','m')])
def test_unit_and_value_cannot_be_relabelled(value,unit):
    doc,candidate=example('面积为100cm²',value,unit)
    result=BusinessValidator().validate(candidate,doc)
    assert result['records'] is None or result['records']['observations'][0]['area_value'] is None


@pytest.mark.parametrize('field',['area_value','area_conversion','area_basis','area_expression','area_rules_version','area_derivation'])
def test_formal_recheck_rejects_tampered_computation(field):
    doc,records=validated('矩形病害，长10厘米、宽10厘米',derived=True)
    o=records['observations'][0]
    if field=='area_derivation':o[field]['inputs'][0]['factor']='100'
    else:o[field]={'area_value':'100','area_conversion':'identity','area_basis':'reported','area_expression':'矩形病害，长20厘米、宽10厘米','area_rules_version':'unknown'}[field]
    assert BusinessValidator().validate_records(records,doc)


def test_graph_scalar_roundtrip_preserves_derivation():
    doc,records=validated('矩形病害，长10厘米、宽10厘米',derived=True)
    props=project_bundle(doc,records)['nodes']['Observation'][0]['properties']
    assert isinstance(props['area_derivation_json'],str)
    decoded=decode_measurement(props)
    assert decoded['area_derivation']==records['observations'][0]['area_derivation']
    assert record_area_valid(decoded,RULES)


def test_conflicting_reported_areas_rejected():
    doc,candidate=example('面积为100cm²，面积为200cm²','100','cm²')
    assert BusinessValidator().validate(candidate,doc)['records'] is None


def test_new_schema_requires_new_expression_field():
    doc,candidate=example('面积为100cm²','100','cm²');candidate['entities'][-1]['data'].pop('area_expression')
    assert BusinessValidator().validate(candidate,doc)['records'] is None


def test_table_header_unit_and_row_value_pair():
    doc,candidate=example('面积为100平方厘米','100','平方厘米')
    def cell(number,text,row,column,header=False):
        return {'block_id':number,'kind':'table_cell','text':text,'source_ref':'#/tables/0/'+number,
            'table_id':'#/tables/0','row':row,'column':column,'column_header':header,
            'locations':[{'page_no':1,'bbox':[.1,.4,.9,.5],'charspan':[0,len(text)]}]}
    head=cell('header','病害面积（平方厘米）',0,1,True)
    code=cell('code','O-TEST',1,0)
    value=cell('value','100',1,1)
    doc['blocks'].extend([head,code,value])
    obs=candidate['entities'][-1]
    for s in obs['sources']:
        if s['target_field']=='area_original_value':s.update(block_id='value',exact_quote='100')
        if s['target_field']=='area_original_unit':s.update(block_id='header',exact_quote=head['text'])
    result=BusinessValidator().validate(candidate,doc)
    assert result['records'] is not None,result['diagnostics']
    assert result['records']['observations'][0]['area_value']=='0.01'
    # A neighbouring row's number cannot stand in for this observation.
    value['row']=2
    assert BusinessValidator().validate(candidate,doc)['records'] is None


def test_confirmed_type_alias_stays_in_existing_taxonomy():
    doc,candidate=example('面积为100cm²','100','cm²')
    original=doc['blocks'][0]['text'];doc['blocks'][0]['text']=original.replace('露筋','钢筋外露')
    doc['blocks'][0]['locations'][0]['charspan'][1]=len(doc['blocks'][0]['text'])
    candidate['entities'][2]['data']['defect_type_raw']='钢筋外露'
    candidate['entities'][-1]['data']['description']=candidate['entities'][-1]['data']['description'].replace('露筋','钢筋外露')
    for owner in candidate['entities']+candidate['relations']:
        for s in owner['sources']:s['exact_quote']=doc['blocks'][0]['text']
    result=BusinessValidator().validate(candidate,doc)
    assert result['records'] is not None,result['diagnostics']
    assert result['records']['defects'][0]['defect_type']=='exposed_rebar'


@pytest.mark.parametrize('derived',[False,True])
def test_answer_new_area_requires_original_or_expression_evidence(derived):
    from ontology_proj.application.answer import AnswerQuestion
    from ontology_proj.application.files import FileStoreError
    _,records=validated('矩形病害，长10厘米、宽10厘米',derived=True) if derived else validated('面积为100平方厘米','100','平方厘米')
    o=records['observations'][0];rid=o['observation_id']
    qualifiers={k:v for k,v in o.items() if k.startswith('area_') or k in ('assertion','scope')}
    targets=['area_expression'] if derived else ['area_original_value','area_original_unit']
    evidence={str(i):{'kind':'fact','record_ids':[rid],'target_field':t,'quote':o['description']} for i,t in enumerate(targets)}
    context={'records':{rid:{'fields':{'area_value':o['area_value'],'observation_code':'O-TEST'},'qualifiers':qualifiers}},'evidence':evidence}
    draft={'status':'answered','reason_codes':[],'claims':[{'kind':'fact','record_ids':[rid],'evidence_ids':list(evidence),
        'assertions':[{'record_id':rid,'field':'area_value','value':'0.01','unit':'m²'}]}]}
    answer=AnswerQuestion(None,None,None,None,None,RULES)
    claim=answer.validate(draft,context)[0]
    assert '0.01 m²' in claim['text']
    if derived:assert '尺寸推算' in claim['text']
    bad=copy.deepcopy(context);bad['records'][rid]['qualifiers']['area_derivation']['inputs'][0]['value']='200'
    with pytest.raises(FileStoreError):answer.validate(draft,bad)
    bad=copy.deepcopy(draft);bad['claims'][0]['evidence_ids']=[]
    with pytest.raises(FileStoreError):answer.validate(bad,context)


@pytest.mark.parametrize('derived',[False,True])
def test_report_persists_new_values_with_citations_and_source_label(tmp_path,derived):
    from test_report import Lookup,Repository,Evidence,FIELDS
    from ontology_proj.application.report import GenerateReport
    from ontology_proj.application.query import LookupGraph
    from ontology_proj.adapters.report_store import LocalReportStore
    _,records=validated('矩形病害，长10厘米、宽10厘米',derived=True) if derived else validated('面积为100平方厘米','100','平方厘米')
    o=records['observations'][0];lookup=Lookup()
    lookup.observation.update({k:v for k,v in o.items() if k not in ('observation_id','component_id','defect_id','evidence_ids')})
    ids=['e'+str(i) for i in range(len(FIELDS))]
    if derived:ids.append('expression')
    lookup.observation['evidence_ids']=ids
    class Reader(Evidence):
        def read(self,eid):
            if eid=='expression':
                return {'evidence':{'evidence_id':eid,'document_id':'doc','quote':o['area_expression'],'page_no':1,'target_field':'area_expression'},
                    'document':{'document_id':'doc','filename':'test.pdf','source_kind':'synthetic'},
                    'records':[{'record_id':'obs','acceptance_status':'accepted','source_acceptance_status':'accepted'}]}
            r=super().read(eid);r['evidence']['quote']=o['description'];return r
    repo=Repository();store=LocalReportStore(tmp_path)
    service=GenerateReport(LookupGraph(lookup,RULES),Reader(),repo,store,RULES)
    rid=service.submit({'bridge_id':'B'})['report_id'];service.run(rid)
    assert repo.get(rid)['status']=='succeeded'
    report,markdown,manifest=store.read(rid)
    row=report['sections'][2]['rows'][0]
    assert row['values']['area_value']=='0.01'
    assert row['values']['area_derivation']==o['area_derivation']
    if derived:
        assert row['field_evidence']['area_expression']==['expression']
        assert '尺寸推算' in markdown and o['area_expression'] in markdown
    else:
        assert row['values']['area_original_unit']=='平方厘米'


def test_query_scaled_area_filter_and_derivation_recheck():
    from ontology_proj.application.query import LookupGraph
    from ontology_proj.application.files import FileStoreError
    from types import SimpleNamespace
    doc,records=validated('面积为100平方厘米','100','平方厘米')
    value=project_bundle(doc,records)['nodes']['Observation'][0]['properties']
    value.update(record_id=value['observation_id'],record_type='Observation')
    use=LookupGraph(SimpleNamespace(lookup=lambda q:[value]),RULES)
    request={'query_type':'component_observations','scope':{'bridge_id':'B-TEST','component_ids':['C-TEST']},'area_min':'0.01','area_max':'0.01'}
    assert len(use.execute(request)['records'])==1
    value['area_value']='100'
    with pytest.raises(FileStoreError):use.execute(request)


def test_fullwidth_matching_keeps_original_source_and_unit():
    doc,records=validated('面积为１００ ｃｍ²','100','ｃｍ²')
    o=records['observations'][0]
    assert o['area_value']=='0.01' and o['area_original_unit']=='ｃｍ²'
    assert '１００ ｃｍ²' in doc['blocks'][0]['text']
    _,records=validated('Ｓ＝２．５×０．９㎡',derived=True)
    o=records['observations'][0]
    assert o['area_value']=='2.25' and o['area_derivation']['inputs'][0]['unit']=='㎡'
    assert o['area_expression']=='Ｓ＝２．５×０．９㎡'


def test_rule_loading_is_bounded_and_alias_conflicts_rejected():
    from ontology_proj.domain.measurement import MeasurementRules
    bad=copy.deepcopy(RULES.data);bad['area_units']['m2']['factor']='100'
    with pytest.raises(ValueError):MeasurementRules(bad)
    bad=copy.deepcopy(RULES.data);bad['area_units']['m2']['factor']='1e99999'
    with pytest.raises(ValueError):MeasurementRules(bad)
    bad=copy.deepcopy(RULES.data);bad['product_operators'].append('eval')
    with pytest.raises(ValueError):MeasurementRules(bad)


@pytest.mark.parametrize('expression', ['S=10厘米×10厘米','面积=0.1米乘以10厘米'])
def test_explicit_area_formula_with_two_length_units(expression):
    _,records=validated(expression,derived=True)
    o=records['observations'][0]
    assert o['area_value']=='0.01' and o['area_basis']=='explicit_area_product'
    assert o['area_original_value'] is None and o['area_original_unit'] is None


@pytest.mark.parametrize('text,expected',[
    ('主梁左侧未见钢筋外露','negative'),('主梁左侧疑似钢筋外露','suspected'),
    ('全桥两处钢筋外露，总面积100cm²','aggregate'),('记录说明，不表示新增钢筋外露','unmapped')])
def test_statement_aliases_preserve_qualifiers(text,expected):
    from ontology_proj.domain.business import statement_kind
    assert statement_kind(text,RULES)==expected


@pytest.mark.parametrize('count',['十一','二十一','零','12','20'])
def test_non_single_chinese_and_arabic_counts_are_not_single_area(count):
    assert RULES.qualifiers('存在'+count+'处露筋，面积100cm²')['aggregate']


def test_short_quote_cannot_discard_source_negation():
    doc,candidate=example('面积为100cm²','100','cm²')
    old=candidate['entities'][-1]['data']['description']
    raw='主梁左侧未见露筋，附近修补面积为100cm²。'
    doc['blocks'][0]['text']=doc['blocks'][0]['text'].replace(old,raw)
    doc['blocks'][0]['locations'][0]['charspan'][1]=len(doc['blocks'][0]['text'])
    cropped='露筋，附近修补面积为100cm²。'
    candidate['entities'][-1]['data']['description']=cropped
    candidate['entities'][2]['data']['location_text']='附近'
    candidate['entities'][-1]['data']['reported_count']=None
    for owner in candidate['entities']+candidate['relations']:
        for source in owner['sources']:
            source['exact_quote']=cropped if source['target_field'] in ('description','assertion','scope','area_original_value','area_original_unit','area_is_approximate') else doc['blocks'][0]['text']
    result=BusinessValidator().validate(candidate,doc)
    assert result['records'] is None
    assert 'DESCRIPTION_QUALIFIER_DROPPED' in {d['code'] for d in result['diagnostics']}
