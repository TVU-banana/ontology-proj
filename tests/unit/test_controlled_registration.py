"""Independent manual fixtures/counterexamples, never real-provider evidence."""
import copy
import hashlib
import json
from types import SimpleNamespace
import pytest
from ontology_proj.adapters.business_validation import BusinessValidator
from ontology_proj.adapters.identity_registry import load_registry
from ontology_proj.application.files import FileStoreError


def fixture():
    def block(bid,text):
        return {'block_id':bid,'kind':'paragraph','text':text,'source_ref':'#/texts/'+bid,
                'locations':[{'page_no':1,'bbox':[0.1,0.1,0.8,0.2],'charspan':[0,len(text)]}]}
    text = '3跨存在1处坑槽 S=0.5m 2'
    doc = {'document_id':'doc_test','source_kind':'real','import_context':{'kind':'inspection_report','bridge_id':'B-MANUAL-A'},
           'pages':[{'page_no':1}],'blocks':[block('b1','工程名称：甲桥'),block('b2','桥面铺装'),block('b3',text)],'tables':[],'warnings':[]}
    reg = {'version':'manual-registration-1','status':'confirmed_by_user','document_id':'doc_test','entries':[
        {'kind':'Bridge','record_id':'B-MANUAL-A','original_code':None,'names':['甲桥'],'sources':[{'block_id':'b1','quote':'甲桥'}]},
        {'kind':'Component','record_id':'C-MANUAL-A','parent_id':'B-MANUAL-A','original_code':None,'names':['桥面铺装'],
         'allowed_component_type':'deck_pavement','defect_location_markers':['3跨'],
         'sources':[{'block_id':'b2','quote':'桥面铺装'}],
         'observation_scopes':[{'block_id':'b3','charspan':[0,len(text)],'quote':text}]}]}
    def source(bid,field):
        return {'block_id':bid,'exact_quote':next(b['text'] for b in doc['blocks'] if b['block_id']==bid),'target_field':field,'occurrence_hint':None}
    def entity(lid,kind,data,bid):
        return {'local_id':lid,'entity_type':kind,'data':data,'sources':[source(bid,f) for f,v in data.items() if v is not None]}
    entities = [entity('bridge','Bridge',{'bridge_code':None,'name':'甲桥'},'b1'),
                entity('deck','Component',{'component_code':None,'name':'桥面铺装','component_type':'deck_pavement','location_text':None},'b2'),
                entity('defect','Defect',{'defect_code':None,'defect_type':'pothole','defect_type_raw':'坑槽','location_text':'3跨'},'b3'),
                entity('observation','Observation',{'observation_code':None,'observed_at':None,'assertion':'positive','scope':'single_defect','reported_count':1,
                       'description':text,'area_original_value':'0.5','area_original_unit':'m 2','area_is_approximate':False},'b3')]
    candidate = {'schema_version':'0.2.0-draft','stage':'candidate','document_id':'doc_test','source_kind':'real','entities':entities,
                 'relations':[{'relation_type':'HAS_COMPONENT','from_ref':'bridge','to_ref':'deck','sources':[source('b2','name')]},
                              {'relation_type':'HAS_DEFECT','from_ref':'deck','to_ref':'defect','sources':[source('b2','name'),source('b3','location_text')]},
                              {'relation_type':'HAS_OBSERVATION','from_ref':'defect','to_ref':'observation','sources':[source('b3','description')]}],
                 'statements':[],'issues':[]}
    return doc,reg,candidate


def test_registered_null_codes_raw_unit_and_authoritative_record_roundtrip():
    doc,reg,candidate = fixture();v = BusinessValidator(reg);result = v.validate(candidate,doc)
    assert result['records'] is not None,result['diagnostics']
    records = result['records']
    assert records['bridges'][0]['bridge_id']=='B-MANUAL-A' and records['bridges'][0]['bridge_code'] is None
    assert records['components'][0]['component_id']=='C-MANUAL-A' and records['components'][0]['component_code'] is None
    obs = records['observations'][0]
    assert obs['area_original_unit']=='m 2' and obs['area_unit']=='m²' and obs['area_value']=='0.5' and obs['observed_at'] is None
    assert not v.validate_records(records,doc)


@pytest.mark.parametrize('mutation',['absent','draft','wrong_document','wrong_parent','wrong_name_source','wrong_span','wrong_context'])
def test_registration_authority_and_source_mismatch_refused(mutation):
    doc,reg,candidate = fixture()
    if mutation=='absent':reg=None
    elif mutation=='draft':reg['status']='draft'
    elif mutation=='wrong_document':reg['document_id']='doc_other'
    elif mutation=='wrong_parent':reg['entries'][1]['parent_id']='B-other'
    elif mutation=='wrong_name_source':reg['entries'][0]['sources'][0]['quote']='乙桥'
    elif mutation=='wrong_span':reg['entries'][1]['observation_scopes'][0]['quote']='wrong'
    else:doc['import_context']['bridge_id']='B-other'
    assert BusinessValidator(reg).validate(candidate,doc)['records'] is None


@pytest.mark.parametrize('mutation',['wrong_area','wrong_location','wrong_type','fake_paper_code','source_outside_scope','negative','suspected','duplicate_mentions'])
def test_bad_model_proposals_not_corrected_or_admitted(mutation):
    doc,reg,candidate = fixture();obs=candidate['entities'][3];comp=candidate['entities'][1]
    if mutation=='wrong_area':obs['data']['area_original_value']='0.9'
    elif mutation=='wrong_location':candidate['entities'][2]['data']['location_text']='4跨'
    elif mutation=='wrong_type':comp['data']['component_type']='girder'
    elif mutation=='fake_paper_code':comp['data']['component_code']='C-MANUAL-A'
    elif mutation=='source_outside_scope':
        other=copy.deepcopy(doc['blocks'][2]);other['block_id']='outside';other['source_ref']='#/texts/outside';doc['blocks'].append(other)
        next(s for s in obs['sources'] if s['target_field']=='area_original_value')['block_id']='outside'
    elif mutation in ('negative','suspected'):
        obs['data']['assertion']=mutation
    else:
        duplicate=copy.deepcopy(obs);duplicate['local_id']='same-location-other-mention';candidate['entities'].append(duplicate)
        relation=copy.deepcopy(candidate['relations'][2]);relation['to_ref']=duplicate['local_id'];candidate['relations'].append(relation)
    before=copy.deepcopy(candidate)
    assert BusinessValidator(reg).validate(candidate,doc)['records'] is None
    assert candidate==before


@pytest.mark.parametrize('mutation',['bridge_id','parent_id','defect_id','observation_id','area_value'])
def test_published_record_tampering_refused(mutation):
    doc,reg,candidate=fixture();v=BusinessValidator(reg);records=v.validate(candidate,doc)['records'];assert records
    if mutation=='bridge_id':records['bridges'][0]['bridge_id']='B-other'
    elif mutation=='parent_id':records['components'][0]['bridge_id']='B-other'
    elif mutation=='defect_id':records['defects'][0]['defect_id']='wrong-defect'
    elif mutation=='observation_id':records['observations'][0]['observation_id']='wrong-observation'
    else:records['observations'][0]['area_value']='0.9'
    assert v.validate_records(records,doc)


def test_registry_loader_pins_bytes_and_confines_path(tmp_path):
    _,reg,_=fixture();p=tmp_path/'registry.json';p.write_text(json.dumps(reg));digest=hashlib.sha256(p.read_bytes()).hexdigest()
    settings=SimpleNamespace(identity_registry_path=p,identity_registry_sha256=digest,data_root=tmp_path)
    assert load_registry(settings)==reg
    p.write_text('{}')
    with pytest.raises(FileStoreError):load_registry(settings)
    settings.data_root=tmp_path/'other'
    with pytest.raises(FileStoreError):load_registry(settings)


def test_legacy_envelope_cannot_bypass_configured_source_registration():
    doc,reg,candidate=fixture();candidate['schema_version']='0.1.0-draft'
    result=BusinessValidator(reg).validate(candidate,doc)
    assert result['records'] is None
    assert result['diagnostics'][0]['code']=='REGISTRATION_VERSION_MISMATCH'


@pytest.mark.parametrize('registered_scope',[True,False])
def test_negative_finding_requires_explicit_registered_scope(registered_scope):
    doc,reg,candidate=fixture();text='桥头平顺，未见明显病害'
    b=copy.deepcopy(doc['blocks'][0]);b.update(block_id='negative',source_ref='#/texts/negative',text=text)
    b['locations'][0]['charspan']=[0,len(text)];doc['blocks'].append(b)
    if registered_scope:
        reg['entries'][1]['statement_scopes']=[{'block_id':'negative','charspan':[0,len(text)],'quote':text}]
    candidate['statements']=[{'kind':'negative','component_ref':'deck','location_text':'桥头','text':text,
                             'sources':[{'block_id':'negative','exact_quote':text,'target_field':f,'occurrence_hint':None}
                                        for f in ('description','kind','component_ref','location_text')]}]
    result=BusinessValidator(reg).validate(candidate,doc)
    assert (result['records'] is not None) is registered_scope
    if registered_scope:assert result['records']['statements'][0]['kind']=='negative'


def test_bound_negative_location_derived_without_mutating_model_candidate():
    doc,reg,candidate=fixture();text='经检查，桥头平顺，未见明显病害。'
    b=copy.deepcopy(doc['blocks'][0]);b.update(block_id='negative',source_ref='#/texts/negative',text=text)
    b['locations'][0]['charspan']=[0,len(text)];doc['blocks'].append(b)
    reg['entries'][1]['statement_scopes']=[{'block_id':'negative','charspan':[0,len(text)],'quote':text}]
    candidate['statements']=[{'kind':'negative','component_ref':'deck','location_text':None,'text':text,
                             'sources':[{'block_id':'negative','exact_quote':text,'target_field':f,'occurrence_hint':None}
                                        for f in ('description','kind','component_ref')]}]
    before=copy.deepcopy(candidate);validator=BusinessValidator(reg);result=validator.validate(candidate,doc)
    assert result['records'] is not None,result['diagnostics']
    assert candidate==before and candidate['statements'][0]['location_text'] is None
    statement=result['records']['statements'][0]
    assert statement['location_text']=='桥头' and any(s['target_field']=='location_text' for s in statement['sources'])
    assert not validator.validate_records(result['records'],doc)
    reg['entries'][1]['statement_scopes']=[]
    assert BusinessValidator(reg).validate(candidate,doc)['records'] is None


@pytest.mark.parametrize('text,location',[
    ('桥头未见明显病害。',None),
    ('桥头平顺，疑似存在病害。',None),
    ('桥头平顺，未见明显病害。不表示全桥无病害。',None),
    ('桥头平顺，未见明显病害。','全桥'),
])
def test_bound_location_rule_never_fills_incomplete_or_conflicting_scope(text,location):
    doc,reg,candidate=fixture();b=copy.deepcopy(doc['blocks'][0]);b.update(block_id='negative',source_ref='#/texts/negative',text=text)
    b['locations'][0]['charspan']=[0,len(text)];doc['blocks'].append(b)
    reg['entries'][1]['statement_scopes']=[{'block_id':'negative','charspan':[0,len(text)],'quote':text}]
    fields=['description','kind','component_ref']+(['location_text'] if location else [])
    candidate['statements']=[{'kind':'negative','component_ref':'deck','location_text':location,'text':text,
                             'sources':[{'block_id':'negative','exact_quote':text,'target_field':f,'occurrence_hint':None} for f in fields]}]
    before=copy.deepcopy(candidate)
    assert BusinessValidator(reg).validate(candidate,doc)['records'] is None
    assert candidate==before


def test_registered_request_shared_fields_roundtrip_and_alias_guard(monkeypatch):
    from ontology_proj.adapters.registered_extractor import RegisteredFactExtractor
    from ontology_proj.adapters.fact_extractor import RelayFactExtractor
    doc,reg,candidate=fixture()
    adapter=RegisteredFactExtractor('http://127.0.0.1:9/v1','fixture','dummy-key',reg)
    request=adapter.request(doc);payload=json.loads(request['messages'][1]['content'])
    for original,row in zip(doc['blocks'],payload['blocks_in_document_order']):
        converted=dict(zip(payload['block_fields'],row))
        assert converted['text']==original['text'] and converted['page_no']==1
        assert adapter.aliases[converted['block_id']]==original['block_id']
    assert request['max_tokens']==64000
    assert all('allowed_component_type' not in e for e in payload['manual_registration']['entries'])
    from jsonschema import Draft202012Validator
    effective=Draft202012Validator(payload['candidate_schema'])
    assert list(effective.iter_errors(candidate))  # Legacy envelope is read-compatible, not a new request format.
    candidate.update(schema_version='0.3.0-draft',identity_mode='registered')
    for entity in candidate['entities']:
        if entity['entity_type']=='Observation':entity['data']['area_expression']=None
    assert not list(effective.iter_errors(candidate))
    fake_code=copy.deepcopy(candidate);fake_code['entities'][3]['data']['observation_code']='copied-local-id'
    assert list(effective.iter_errors(fake_code))
    wrong_type=copy.deepcopy(candidate);wrong_type['entities'][1]['data']['component_type']='girder'
    assert list(effective.iter_errors(wrong_type))
    monkeypatch.setattr(RelayFactExtractor,'extract',lambda *args:{'candidate':copy.deepcopy(candidate),'response':{'fixture':True},'diagnostics':[]})
    assert adapter.extract(request)['candidate']==candidate
    candidate['entities'][0]['sources'][0]['block_id']='invented'
    assert adapter.extract(request)['candidate'] is None


def test_registered_parent_relation_can_use_its_owned_observation_source():
    doc,reg,candidate=fixture()
    candidate['relations'][1]['sources']=[candidate['relations'][1]['sources'][1]]
    assert BusinessValidator(reg).validate(candidate,doc)['records'] is not None
