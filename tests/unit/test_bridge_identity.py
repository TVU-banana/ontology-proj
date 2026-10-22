import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from ontology_proj.domain.bridge_identity import decision,source_facts,snapshot_hash
from ontology_proj.adapters.bridge_catalog import BridgeCatalog
from ontology_proj.adapters.business_validation import BusinessValidator
from ontology_proj.adapters.catalog_extractor import CatalogFactExtractor
from ontology_proj.application.ingestion import IngestionService
from ontology_proj.application.files import FileStoreError
from ontology_proj.domain.graph_mapping import project_bundle
from ontology_proj.interfaces.http.app import create_http_app


def doc(n=1,code=None,name='胜利大桥',location='浙江省平湖市某路',namespace='X',source_kind='real'):
    fields=[('桥梁名称',name),('地理位置',location),('桥梁编号',code),('编号体系',namespace if code else None)]
    text='\n'.join(k+'：'+v for k,v in fields if v is not None)
    return {'document_id':'doc_'+f'{n:064x}','source_kind':source_kind,'import_context':{'bridge_id':None},'blocks':[{'block_id':'b1','text':text}]}


def register(catalog,**kw):
    return catalog.register(dict(name='胜利大桥',location='浙江省平湖市某路',official_code='123',official_namespace='X',source_kind='real',**kw))['bridge']


def test_three_reports_one_stable_id_and_restart(tmp_path):
    catalog=BridgeCatalog(tmp_path);a=register(catalog)
    assert a['bridge_id']!='123'
    for d,chosen in [(doc(1,'123'),None),(doc(2),None),(doc(3,name=None,location=None),a['bridge_id'])]:
        r=catalog.resolve(d,chosen);assert r['status']=='resolved' and r['bridge_id']==a['bridge_id']
        assert BridgeCatalog(tmp_path).binding(d)['bridge_id']==a['bridge_id']
    assert len(catalog.list())==1


@pytest.mark.parametrize('change,reason',[
    ({'name':'胜利'},'BRIDGE_MATCH_MISSING'),({'location':'平湖市'},'BRIDGE_MATCH_MISSING'),
    ({'location':None},'BRIDGE_MATCH_MISSING'),({'name':None},'BRIDGE_MATCH_MISSING'),
    ({'source_kind':'synthetic'},'BRIDGE_MATCH_MISSING'),({'code':'456'},'BRIDGE_OFFICIAL_CONFLICT'),
    ({'code':'123','namespace':None},'BRIDGE_CODE_NAMESPACE_REQUIRED')])
def test_unsafe_matches_pause(tmp_path,change,reason):
    c=BridgeCatalog(tmp_path);register(c)
    assert c.resolve(doc(**change))['reason']==reason
    with pytest.raises(FileStoreError,match='confirmation'):c.binding(doc(**change))


def test_normalization_exact_only_and_no_new_bridge(tmp_path):
    c=BridgeCatalog(tmp_path);a=register(c)
    assert c.resolve(doc(name='胜 利 大 桥',location='浙江省 平湖市 某路'))['bridge_id']==a['bridge_id']
    assert len(c.list())==1
    assert c.resolve(doc(2,name='另一桥',location='另一地址'))['status']=='needs_confirmation'
    assert len(c.list())==1


def test_selected_bridge_and_official_conflict(tmp_path):
    c=BridgeCatalog(tmp_path);a=register(c)
    b=c.register(dict(name='另一桥',location='另一址',source_kind='real',official_code='456',official_namespace='X'))['bridge']
    assert c.resolve(doc(code='456',name='另一桥',location='另一址'),a['bridge_id'])['reason']=='BRIDGE_SELECTION_CONFLICT'
    assert c.resolve(doc(2,code='999'),a['bridge_id'])['reason']=='BRIDGE_OFFICIAL_CONFLICT'
    assert c.resolve(doc(3),'unknown')['reason']=='BRIDGE_SELECTION_UNKNOWN'
    # Explicit operator selection supplies scope for an otherwise unscoped code.
    assert c.resolve(doc(4,code='123',namespace=None),a['bridge_id'])['status']=='resolved'


def test_same_name_different_address_kept_separate(tmp_path):
    c=BridgeCatalog(tmp_path);a=register(c)
    b=c.register(dict(name='胜利大桥',location='上海市另一址',source_kind='real'))['bridge']
    assert a['bridge_id']!=b['bridge_id']
    assert c.resolve(doc(location='上海市另一址'))['bridge_id']==b['bridge_id']


def test_later_official_code_enriches_master_without_changing_id(tmp_path):
    c=BridgeCatalog(tmp_path);a=c.register(dict(name='胜利大桥',location='浙江省平湖市某路',source_kind='real'))['bridge']
    first=doc(1);assert c.resolve(first)['bridge_id']==a['bridge_id']
    assert c.resolve(doc(2,code='123'))['bridge_id']==a['bridge_id']
    assert c.binding(first)['bridge_id']==a['bridge_id']
    assert c.list()[0]['official_ids'][0]['origin']==doc(2)['document_id']


def test_source_or_binding_cannot_be_reassigned(tmp_path):
    c=BridgeCatalog(tmp_path);register(c);d=doc();c.resolve(d)
    with pytest.raises(FileStoreError) as err:c.resolve(d,'other')
    assert err.value.code=='BRIDGE_BINDING_IMMUTABLE'
    d['blocks'][0]['text']+=' changed'
    with pytest.raises(FileStoreError) as err:c.binding(d)
    assert err.value.code=='BRIDGE_BINDING_SOURCE_CHANGED'


def test_ambiguous_source_and_catalog_pause(tmp_path):
    c=BridgeCatalog(tmp_path);a=register(c);d=doc();d['blocks'][0]['text']+='\n桥名：另一桥'
    assert c.resolve(d)['reason']=='BRIDGE_SOURCE_AMBIGUOUS'
    facts=source_facts(doc());b=copy.deepcopy(a);b['bridge_id']='other'
    assert decision([a,b],facts,'real')['reason']=='BRIDGE_MATCH_AMBIGUOUS'
    assert decision([a,b],facts,'real',a['bridge_id'])['status']=='resolved'


def test_concurrent_registration_is_atomic_and_unique(tmp_path):
    with ThreadPoolExecutor(max_workers=6) as p:
        rows=list(p.map(lambda _:register(BridgeCatalog(tmp_path)),range(12)))
    assert len({b['bridge_id'] for b in rows})==1 and len(BridgeCatalog(tmp_path).list())==1


def test_symlink_state_and_invalid_metadata_rejected(tmp_path):
    c=BridgeCatalog(tmp_path)
    with pytest.raises(FileStoreError):c.register(dict(name='桥',location='位置',source_kind='real',unexpected='x'))
    root=tmp_path/'bridge-identities';root.mkdir();(root/'state.json').symlink_to(tmp_path/'outside')
    with pytest.raises(FileStoreError) as e:c.list()
    assert e.value.code=='UNSAFE_STORAGE_PATH'


def business_doc(n=1,code='B-TEST'):
    from test_measurement_rules import example
    document,candidate=example('面积100平方厘米','100','平方厘米')
    document.update(document_id='doc_'+f'{n:064x}',pages=[{'page_no':1}],tables=[],warnings=[],import_context={'bridge_id':None})
    text=document['blocks'][0]['text']
    if code is None:
        text=text.replace('测试桥 B-TEST','测试桥')
        for item in candidate['entities']+candidate['relations']:
            for source in item['sources']:source['exact_quote']=source['exact_quote'].replace('测试桥 B-TEST','测试桥')
    text+='\n桥梁名称：测试桥\n地理位置：测试省测试市测试路'
    if code is not None:text+='\n桥梁编号：'+code+'\n编号体系：TEST'
    document['blocks'][0]['text']=text;document['blocks'][0]['locations'][0]['charspan'][1]=len(text)
    candidate.update(document_id=document['document_id'],schema_version='0.4.0-draft',identity_mode='catalog')
    candidate['entities'][0]['data']['bridge_code']=code
    candidate['entities'][0]['sources']=[s for s in candidate['entities'][0]['sources'] if code is not None or s['target_field']!='bridge_code']
    return document,candidate


def business_catalog(tmp_path):
    c=BridgeCatalog(tmp_path)
    a=c.register(dict(name='测试桥',location='测试省测试市测试路',source_kind='synthetic',official_code='B-TEST',official_namespace='TEST'))['bridge']
    return c,a


def test_catalog_candidate_ids_evidence_and_document_local_components(tmp_path):
    c,a=business_catalog(tmp_path);v=BusinessValidator(catalog=c);ids=[]
    for n,code in [(1,'B-TEST'),(2,None)]:
        d,candidate=business_doc(n,code);binding=c.resolve(d);result=v.validate(candidate,d)
        assert result['records'] is not None,result['diagnostics']
        records=result['records'];assert records['bridges'][0]['bridge_id']==a['bridge_id']
        assert records['bridges'][0]['bridge_code']==code and not v.validate_records(records,d)
        ids.append(records['components'][0]['component_id'])
        projection=project_bundle({**d,'bridge_binding':binding},records)
        assert projection['nodes']['Bridge'][0]['properties']['bridge_code'] is None
        assert projection['nodes']['Bridge'][0]['properties']['identity_version']=='bridge-identity-1'
    assert ids[0]!=ids[1]


@pytest.mark.parametrize('field,value',[('bridge_code','made-up'),('name','another')])
def test_candidate_cannot_override_bridge_identity(tmp_path,field,value):
    c,a=business_catalog(tmp_path);d,b=business_doc();c.resolve(d);b['entities'][0]['data'][field]=value
    result=BusinessValidator(catalog=c).validate(b,d)
    assert result['records'] is None and any(i['code']=='BRIDGE_CANDIDATE_CONFLICT' for i in result['diagnostics'])


def test_records_binding_tamper_and_envelope_bypass(tmp_path):
    c,a=business_catalog(tmp_path);d,b=business_doc();c.resolve(d);v=BusinessValidator(catalog=c)
    records=v.validate(b,d)['records'];records['bridges'][0]['bridge_id']='made-up'
    assert any(i['code']=='RECORD_BRIDGE_CONFLICT' for i in v.validate_records(records,d))
    b['schema_version']='0.3.0-draft';assert v.validate(b,d)['records'] is None
    assert BusinessValidator().validate(business_doc()[1],d)['records'] is None


def test_new_request_no_sample_whitelist_and_no_false_original_code(tmp_path):
    c,a=business_catalog(tmp_path);d,b=business_doc(code=None);c.resolve(d)
    extractor=CatalogFactExtractor('http://example.invalid/v1','model','secret',c)
    request=extractor.request(d);payload=json.loads(request['messages'][1]['content'])
    assert payload['trusted_context']['identity_mode']=='catalog'
    assert payload['trusted_context']['bridge_binding']['bridge_id']==a['bridge_id']
    assert payload['candidate_schema']['$defs']['BridgeCandidateData']['properties']['bridge_code'].get('type')!='null'
    assert 'enum' not in payload['candidate_schema']['$defs']['ComponentCandidateData']['properties']['name']
    assert 'secret' not in json.dumps(request)
    assert extractor.configuration_for(d['document_id'])['schema_version']=='0.4.0-draft'


def test_pipeline_pauses_before_model_then_retries_saved_parse(tmp_path):
    c=BridgeCatalog(tmp_path);d,b=business_doc(code=None);events=[]
    class Store:
        def read_artifact(self,*args):return d
        def read_business(self,*args):return None
        def interrupt_business_runs(self,*args):pass
        def business_artifact_keys(self,*args):return []
        def read_business_artifact(self,*args):return b
    class Graph:
        def operation(self,op):return {'document_id':d['document_id'],'retry_of':'previous'}
        def advance(self,*a):events.append(a[1])
        def fail(self,op,code,*args):events.append(code)
        def commit_bundle(self,op,doc,records,artifacts):events.append(doc['bridge_binding']['bridge_id'])
    parsing=SimpleNamespace(process=lambda *args,**kw:{'reused':True})
    extraction=SimpleNamespace(execute=lambda *args,**kw:events.append('model'),validator=SimpleNamespace(validate_records=lambda *args:[]))
    service=IngestionService(Store(),parsing,extraction,Graph(),catalog=c)
    service.run('op');assert 'model' not in events and events[-1]=='BRIDGE_CONFIRMATION_REQUIRED'
    a=c.register(dict(name='测试桥',location='测试省测试市测试路',source_kind='synthetic'))['bridge']
    c.resolve(d)
    records={'identity_mode':'catalog'}
    service.store.read_business_artifact=lambda *a:records
    extraction.execute=lambda *a,**k:{'status':'succeeded','run_id':'r'}
    service.run('op');assert events[-1]==a['bridge_id']


def test_http_registration_resolution_and_old_publication_preserved(tmp_path):
    c=BridgeCatalog(tmp_path);d=doc()
    service=IngestionService(SimpleNamespace(read_artifact=lambda *a:d,read_business=lambda *a:None),None,None,None,catalog=c)
    service._initialized=True  # HTTP fixture represents an already initialized graph/runner.
    service.start=lambda:None;service.close=lambda:None
    app=create_http_app(None,service)
    with TestClient(app) as client:
        assert client.get('/api/bridges').json()['bridges']==[]
        body=dict(name='胜利大桥',location='浙江省平湖市某路',source_kind='real')
        a=client.post('/api/bridges',json=body).json()['bridge']
        assert client.post('/api/bridges',json=body).json()['duplicate'] is True
        route='/api/documents/'+d['document_id']+'/bridge-resolution'
        assert client.get(route).json()['status']=='not_resolved'
        assert client.post(route,json={}).json()['bridge_id']==a['bridge_id']
        assert client.post(route,json={'sql':'bad'}).status_code==422
        service.store.read_business=lambda *a:{'published':True}
        service.store.read_business_artifact=lambda *a:{'bridges':[{'bridge_id':'legacy-ID'}]}
        assert client.post(route,json={'selected_bridge_id':'other'}).json()['status']=='legacy_preserved'


def test_operator_adds_scoped_code_to_existing_bridge(tmp_path):
    c=BridgeCatalog(tmp_path)
    a=c.register(dict(name='胜利大桥',location='浙江省平湖市某路',source_kind='real'))['bridge']
    b=register(c)
    assert b['bridge_id']==a['bridge_id'] and b['official_ids'][0]['code']=='123'


def test_duplicate_upload_with_resolved_selection_preserves_original_source_context(tmp_path):
    from ontology_proj.adapters.ingestion_store import IngestionStore
    from ontology_proj.application.dto import ImportContext
    from ontology_proj.domain.source import DocumentKind,SourceKind
    root=Path(__file__).resolve().parents[2]
    pdf=(root/'samples/synthetic/bridge-demo-001/input/bridge-demo-001.pdf').read_bytes()
    store=IngestionStore(tmp_path);original=ImportContext(DocumentKind.INSPECTION_REPORT,SourceKind.SYNTHETIC)
    source=store.save_source(pdf,'demo.pdf',original).source
    d=doc(source_kind='synthetic');d['document_id']=source.document_id
    # Saved source validation is real; the published parse read is an explicit fixture double.
    store.read_artifact=lambda *args:d
    c=BridgeCatalog(tmp_path);a=c.register(dict(name='胜利大桥',location='浙江省平湖市某路',source_kind='synthetic'))['bridge'];c.resolve(d)
    g=SimpleNamespace(initialize=lambda:None,recover=lambda:None,replay=lambda *a:None,register=lambda props,request:{'document_id':props['document_id'],'duplicate':True,'bridge_id':props['bridge_id']})
    service=IngestionService(store,SimpleNamespace(recover=lambda:None),None,g,catalog=c)
    result=service.upload(pdf,'other-filename.pdf',ImportContext(DocumentKind.INSPECTION_REPORT,SourceKind.SYNTHETIC,a['bridge_id']),'duplicate-key')
    assert result['duplicate'] and result['bridge_id'] is None
    assert store.read_source(source.document_id).context==original


def test_source_official_number_never_means_report_number(tmp_path):
    c=BridgeCatalog(tmp_path);register(c);d=doc()
    d['blocks'][0]['text']='检测报告编号：123\n项目编号：123\n桥梁名称：胜利大桥'
    assert source_facts(d)['values']['official_code'] is None
    assert c.resolve(d)['status']=='needs_confirmation'


def test_relation_requires_joint_bridge_name_and_component_evidence(tmp_path):
    c,a=business_catalog(tmp_path);d,b=business_doc(code=None);c.resolve(d)
    # Replace the proposed bridge/component relation quote with an area-only sentence.
    b['relations'][0]['sources'][0]['exact_quote']='面积100平方厘米'
    assert BusinessValidator(catalog=c).validate(b,d)['records'] is None


def test_conflicting_new_bridge_registration_refused(tmp_path):
    c=BridgeCatalog(tmp_path);register(c)
    with pytest.raises(FileStoreError) as e:
        c.register(dict(name='胜利大桥',location='浙江省平湖市某路',official_code='456',official_namespace='X',source_kind='real'))
    assert e.value.code=='BRIDGE_REGISTRATION_CONFLICT'


def test_label_value_table_cells_have_source_proof(tmp_path):
    c=BridgeCatalog(tmp_path);a=register(c);d=doc();d['blocks']=[]
    for row,(key,value) in enumerate([('桥名','胜利大桥'),('桥址','浙江省平湖市某路')]):
        for col,text in enumerate([key,value]):d['blocks'].append(dict(block_id=f'b{row}{col}',kind='table_cell',table_id='t',row=row,column=col,row_span=1,column_span=1,text=text))
    assert c.resolve(d)['bridge_id']==a['bridge_id']
    assert c.binding(d)['facts']['evidence']['name'][0]['label_source']['quote']=='桥名'
    d=copy.deepcopy(d);d['document_id']='doc_'+f'{2:064x}';d['blocks'][1]['row']=4
    assert c.resolve(d)['status']=='needs_confirmation'


def test_graph_commit_records_binding_and_does_not_conflict_on_missing_original_code(tmp_path):
    from ontology_proj.adapters.neo4j_repository import Neo4jRepository
    c,a=business_catalog(tmp_path);d,b=business_doc(code=None);binding=c.resolve(d)
    records=BusinessValidator(catalog=c).validate(b,d)['records'];effective={**d,'bridge_binding':binding};projection=project_bundle(effective,records)
    writes=[]
    class Result:
        def __init__(self,row=None):self.row=row
        def single(self):return self.row
        def consume(self):pass
    class Tx:
        def run(self,q,**kw):
            writes.append((q,kw))
            if 'AS op' in q:return Result({'op':{'status':'running','actor':'test'},'doc':{'document_id':d['document_id'],'source_kind':'synthetic','processing_status':'extracting','operation_id':'op'}})
            if 'MATCH (n:Bridge ' in q:return Result({'value':projection['nodes']['Bridge'][0]['properties']})
            return Result()
    repo=Neo4jRepository(None,None,None)
    result=repo._commit(Tx(),'op',effective,records,projection,[],'audit')
    assert result['bridge_id']==a['bridge_id'] and 'OBJECT_CONFLICT' not in result['warnings']
    assert any('ABOUT_BRIDGE' in q for q,kw in writes)
    assert result['source_bridge_code'] is None and result['bridge_resolution_source_sha256']==snapshot_hash(d)


def test_resolved_document_cannot_downgrade_to_explicit_code_identity(tmp_path):
    c,a=business_catalog(tmp_path);d,b=business_doc();c.resolve(d);v=BusinessValidator(catalog=c)
    records=v.validate(b,d)['records']
    b.update(schema_version='0.3.0-draft',identity_mode='explicit')
    assert v.validate(b,d)['diagnostics'][0]['code']=='CATALOG_VERSION_REQUIRED'
    records.update(schema_version='0.3.0-draft',identity_mode='explicit')
    assert v.validate_records(records,d)[0]['code']=='RECORD_CATALOG_VERSION_REQUIRED'


def test_new_document_does_not_inherit_legacy_registration(tmp_path):
    # The source-specific legacy object may exist, but only its exact document is routed to it.
    c,a=business_catalog(tmp_path);d,b=business_doc();c.resolve(d)
    legacy={'document_id':'doc_'+f'{999:064x}','entries':[]}
    extractor=CatalogFactExtractor('http://example.invalid/v1','model','fake-key',c,legacy)
    request=extractor.request(d);payload=json.loads(request['messages'][1]['content'])
    assert 'manual_registration' not in payload and payload['trusted_context']['identity_mode']=='catalog'
    assert extractor.configuration_for(d['document_id'])['identity_mode']=='catalog'
    assert extractor.configuration_for(legacy['document_id'])['identity_mode']=='registered'


def test_official_codes_do_not_assume_case_equivalence(tmp_path):
    c=BridgeCatalog(tmp_path)
    c.register(dict(name='胜利大桥',location='浙江省平湖市某路',source_kind='real',official_namespace='X',official_code='ABC'))
    assert c.resolve(doc(code='abc'))['reason']=='BRIDGE_OFFICIAL_CONFLICT'
    d=doc(2,code='ABC');d['blocks'][0]['text']+='\n桥梁编号：abc'
    assert c.resolve(d)['reason']=='BRIDGE_SOURCE_AMBIGUOUS'


def test_existing_published_id_adopted_and_location_enriched_without_rewriting_old_records(tmp_path):
    c=BridgeCatalog(tmp_path);record={'bridge_id':'B-MANUAL-EXISTING','bridge_code':None,'name':'胜利大桥','source_document_id':'doc_old','validation_status':'passed','acceptance_status':'accepted'}
    before=copy.deepcopy(record);a=c.adopt_existing(record,'real');assert a['bridge_id']==record['bridge_id'] and a['location'] is None
    d=doc(1,name=None,location=None);assert c.resolve(d,a['bridge_id'])['bridge_id']==a['bridge_id']
    d2=doc(2);assert c.resolve(d2,a['bridge_id'])['bridge_id']==a['bridge_id']
    assert c.binding(d)['bridge_id']==a['bridge_id']
    assert c.resolve(doc(3))['reason']=='name_location'
    assert c.list()[0]['location']=='浙江省平湖市某路' and record==before


def test_runner_imports_only_verified_local_published_bridges(tmp_path):
    c=BridgeCatalog(tmp_path);d,b=business_doc();b.update(schema_version='0.3.0-draft',identity_mode='explicit')
    records=BusinessValidator().validate(b,d)['records'];bridge=records['bridges'][0]
    store=SimpleNamespace(read_artifact=lambda *a:d,read_business_artifact=lambda *a:records)
    graph=SimpleNamespace(initialize=lambda:None,recover=lambda:None,existing_bridges=lambda:[bridge,{**bridge,'bridge_id':'foreign'}])
    service=IngestionService(store,SimpleNamespace(recover=lambda:None),None,graph,catalog=c,validator=BusinessValidator(catalog=c))
    service._ensure();assert [r['bridge_id'] for r in c.list()]==[bridge['bridge_id']]


def test_imported_legacy_bridge_conflict_keeps_source_code_boundary(tmp_path):
    c=BridgeCatalog(tmp_path)
    a=c.adopt_existing({'bridge_id':'old-123','bridge_code':'123','name':'胜利大桥','source_document_id':'doc_old','validation_status':'passed','acceptance_status':'accepted'},'real')
    assert c.resolve(doc(1,code='456'),a['bridge_id'])['reason']=='BRIDGE_OFFICIAL_CONFLICT'
    assert c.resolve(doc(2,code='123',namespace=None),a['bridge_id'])['status']=='resolved'


def test_manual_detail_confirmation_does_not_override_official_conflict(tmp_path):
    c=BridgeCatalog(tmp_path);a=register(c);d=doc(name='胜利桥',location=None)
    assert c.resolve(d,a['bridge_id'])['reason']=='BRIDGE_DETAILS_CONFLICT'
    result=c.resolve(d,a['bridge_id'],confirm_details=True)
    assert result['status']=='resolved' and result['reason']=='user_confirmation'
    assert c.binding(d)['confirmed_details'] and c.list()[0]['name']=='胜利大桥'
    assert c.resolve(doc(2,code='456'),a['bridge_id'],True)['reason']=='BRIDGE_OFFICIAL_CONFLICT'


def test_detail_confirmation_requires_explicit_boolean_and_selection(tmp_path):
    c=BridgeCatalog(tmp_path);register(c)
    with pytest.raises(FileStoreError):c.resolve(doc(),None,True)
    with pytest.raises(FileStoreError):c.resolve(doc(),None,'yes')


def test_graph_reuses_imported_legacy_bridge_without_overwriting_original_properties(tmp_path):
    from ontology_proj.adapters.neo4j_repository import Neo4jRepository
    c=BridgeCatalog(tmp_path);old={'bridge_id':'legacy-B','bridge_code':'B-TEST','name':'测试桥','source_document_id':'doc_old','validation_status':'passed','acceptance_status':'accepted'}
    before=copy.deepcopy(old);c.adopt_existing(old,'synthetic');d,b=business_doc(code=None);binding=c.resolve(d,'legacy-B')
    records=BusinessValidator(catalog=c).validate(b,d)['records'];effective={**d,'bridge_binding':binding};projection=project_bundle(effective,records)
    class Result:
        def __init__(self,row=None):self.row=row
        def single(self):return self.row
        def consume(self):pass
    class Tx:
        def run(self,q,**kw):
            if 'AS op' in q:return Result({'op':{'status':'running','actor':'test'},'doc':{'document_id':d['document_id'],'source_kind':'synthetic','processing_status':'extracting','operation_id':'op'}})
            if 'MATCH (n:Bridge ' in q:return Result({'value':old})
            return Result()
    result=Neo4jRepository(None,None,None)._commit(Tx(),'op',effective,records,projection,[],'audit')
    assert 'OBJECT_CONFLICT' not in result['warnings'] and result['bridge_id']=='legacy-B' and old==before


def test_new_web_registration_waits_for_legacy_discovery_when_graph_unavailable(tmp_path):
    c=BridgeCatalog(tmp_path)
    def unavailable():raise FileStoreError('GRAPH_UNAVAILABLE','Graph unavailable')
    service=IngestionService(None,None,None,SimpleNamespace(initialize=unavailable),catalog=c)
    with pytest.raises(FileStoreError) as e:service.register_bridge(dict(name='胜利大桥',location='浙江省平湖市某路',source_kind='real'))
    assert e.value.code=='GRAPH_UNAVAILABLE' and c.list()==[]
