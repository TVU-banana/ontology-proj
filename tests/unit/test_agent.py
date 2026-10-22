from copy import deepcopy
import json
from types import SimpleNamespace
import pytest
from ontology_proj.application.agent import RunAgent
from ontology_proj.application.files import FileStoreError
from ontology_proj.adapters.agent_journal import LocalAgentJournal
from ontology_proj.application.query import LookupGraph


def call(name,args=None):
    return {'choices':[{'finish_reason':'tool_calls','message':{'role':'assistant','content':None,'tool_calls':[{'id':'call_1','type':'function','function':{'name':name,'arguments':json.dumps(args or {})}}]}}]}


def stop():return {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'任意未核验内容不展示'}}]}


class Provider:
    def __init__(self,responses):self.responses=iter(responses);self.requests=[]
    def request(self,messages,tools):return {'messages':deepcopy(messages),'tools':tools}
    def complete(self,request):self.requests.append(request);return next(self.responses)


class Journal:
    def __init__(self):self.artifacts={}
    def save(self,rid,phase,data):self.artifacts[phase]=deepcopy(data)


@pytest.fixture
def setup():
    docs=[];writes=[];queries=[]
    ingestion=SimpleNamespace(document=lambda did:docs.append(did) or {'document_id':did,'processing_status':'succeeded','pdf_key':'private'},
        request_processing=lambda did,key:writes.append((did,key)) or {'operation_id':'op','status':'queued'},
        operation=lambda oid:{'document_id':'another','operation_id':oid})
    lookup=LookupGraph(SimpleNamespace(lookup=lambda q:queries.append(q) or []))
    return ingestion,lookup,docs,writes,queries


def agent(setup,responses,journal=None,**services):
    ingestion,lookup,*_=setup;p=Provider(responses);j=journal or Journal()
    return RunAgent(p,j,ingestion,lookup,services.get('semantic'),services.get('evidence'),services.get('answer'),services.get('reports')),p,j


def request():return {'instruction':'查询构件','scope':{'document_ids':['doc1']}}


def test_native_call_feedback_and_no_unchecked_answer(setup):
    a,p,j=agent(setup,[call('graph_lookup',{'query_type':'bridge_components'}),stop()]);r=a.execute('knowledge',request())
    assert r['status']=='completed' and r['events'][0]['result']['ok']
    assert '任意' not in json.dumps(r,ensure_ascii=False)
    assert p.requests[1]['messages'][-1]['role']=='tool'
    assert j.artifacts['tool-1']['request_id']==r['events'][0]['request_id']
    assert setup[-1][0]['scope']['document_ids']==['doc1']


@pytest.mark.parametrize('role,name,body',[
    ('ingestion','generate_report',{'instruction':'报告','document_id':'doc1'}),
    ('knowledge','process_document',request())])
def test_role_tools_cannot_escalate(setup,role,name,body):
    a,p,j=agent(setup,[call(name)]);r=a.execute(role,body)
    assert r['error']['code']=='AGENT_TOOL_FORBIDDEN' and not setup[3]


@pytest.mark.parametrize('args',[{'query_type':'bridge_components','scope':{'document_ids':['other']}}, {'query_type':'raw_cypher'}, {'query_type':'bridge_components','role':'admin'}])
def test_bad_queries_no_dispatch(setup,args):
    a,p,j=agent(setup,[call('graph_lookup',args)]);r=a.execute('knowledge',request())
    assert r['status']=='failed' and r['error']['code']=='INVALID_REQUEST' and not setup[-1]


def test_repeated_write_stops_before_second_mutation(setup):
    a,p,j=agent(setup,[call('process_document'),call('process_document')]);r=a.execute('ingestion',{'instruction':'处理资料','document_id':'doc1'})
    assert len(setup[3])==1 and r['error']['code']=='AGENT_REPEATED_CALL'


def test_foreign_operation_rejected(setup):
    a,p,j=agent(setup,[call('get_operation',{'operation_id':'op'})]);r=a.execute('ingestion',{'instruction':'读取操作','document_id':'doc1'})
    assert r['error']['code']=='AGENT_SCOPE_VIOLATION'


def test_components_cannot_broaden_scope(setup):
    a,p,j=agent(setup,[call('graph_lookup',{'query_type':'component_observations','component_ids':['other']})]);body=request();body['scope']['component_ids']=['c1'];r=a.execute('knowledge',body)
    assert r['error']['code']=='AGENT_SCOPE_VIOLATION'


def test_no_scope_fails_before_provider(setup):
    a,p,j=agent(setup,[])
    with pytest.raises(FileStoreError):a.execute('knowledge',{'instruction':'全库查询','scope':{}})
    assert not p.requests and not j.artifacts


def test_same_invalid_query_shared_with_http(setup):
    args={'query_type':'component_observations'}
    with pytest.raises(FileStoreError) as e:setup[1].execute({**args,'scope':{'document_ids':['doc1']}})
    a,p,j=agent(setup,[call('graph_lookup',args)]);r=a.execute('knowledge',request())
    assert r['error']['code']==e.value.code


def test_foreign_evidence_not_returned(setup):
    evidence=SimpleNamespace(read=lambda eid:{'records':[{'record_id':'foreign'}],'block_text':'secret'})
    a,p,j=agent(setup,[call('get_evidence',{'evidence_id':'e1'})],evidence=evidence);r=a.execute('knowledge',request())
    assert r['error']['code']=='AGENT_SCOPE_VIOLATION' and 'secret' not in json.dumps(r)


@pytest.mark.parametrize('modify',[
    lambda r:r['choices'][0].update(finish_reason='length'),
    lambda r:r['choices'][0]['message']['tool_calls'].append(deepcopy(r['choices'][0]['message']['tool_calls'][0])),
    lambda r:r['choices'][0]['message']['tool_calls'][0]['function'].update(arguments='{"query_type":"bridge_components","query_type":"record_by_id"}'),
])
def test_bad_response_never_dispatches(setup,modify):
    response=call('graph_lookup',{'query_type':'bridge_components'});modify(response)
    a,p,j=agent(setup,[response]);r=a.execute('knowledge',request())
    assert r['status']=='failed' and not setup[-1] and 'summary' in j.artifacts


def test_step_limit_preserves_eight_calls(setup):
    semantic=SimpleNamespace(execute=lambda args:{'hits':[]})
    a,p,j=agent(setup,[call('semantic_search',{'query':str(i)}) for i in range(8)],semantic=semantic);r=a.execute('knowledge',request())
    assert len(r['events'])==8 and r['error']['code']=='AGENT_STEP_LIMIT' and len(p.requests)==8


def test_journal_immutable(tmp_path):
    from uuid import uuid4
    rid=str(uuid4());j=LocalAgentJournal(tmp_path);j.save(rid,'tool-1',{'ok':True})
    with pytest.raises(FileStoreError):j.save(rid,'tool-1',{'ok':False})
    assert json.loads((tmp_path/'agents'/rid/'tool-1.json').read_text())=={'ok':True}


def test_ingestion_result_does_not_send_storage_paths(setup):
    a,p,j=agent(setup,[call('get_ingestion_result'),stop()]);r=a.execute('ingestion',{'instruction':'读取结果','document_id':'doc1'})
    assert r['status']=='completed' and 'private' not in json.dumps(r) and 'private' not in json.dumps(p.requests)


def test_transport_failure_saved_without_retry(setup):
    a,p,j=agent(setup,[])
    def failed(request):
        p.requests.append(request)
        raise FileStoreError('MODEL_TRANSPORT_FAILED','transport failed')
    p.complete=failed;r=a.execute('knowledge',request())
    assert r['error']['code']=='MODEL_TRANSPORT_FAILED' and len(p.requests)==1 and j.artifacts['summary']['status']=='failed'


def test_current_evidence_acceptance_rechecked(setup):
    evidence=SimpleNamespace(read=lambda eid:{'records':[{'record_id':'r1'}],'block_text':'quote'})
    setup[1].repository.lookup=lambda q:[{'record_id':'r1','record_type':'Observation','evidence_ids':['e1']}]
    a,p,j=agent(setup,[call('get_evidence',{'evidence_id':'e1'}),stop()],evidence=evidence)
    body=request();body['scope']['date_from']='2026-01-01';r=a.execute('knowledge',body)
    assert r['error']['code']=='AGENT_SCOPE_VIOLATION'


def test_http_roles_are_server_bound_and_workspace_served(setup):
    from fastapi.testclient import TestClient
    from pathlib import Path
    from ontology_proj.interfaces.http.app import create_http_app
    a,p,j=agent(setup,[call('get_ingestion_result'),stop()])
    app=create_http_app(None,agents=a,static_directory=Path('src/ontology_proj/interfaces/http/static'))
    with TestClient(app) as client:
        response=client.post('/api/agents/ingestion',json={'instruction':'状态','document_id':'doc1'})
        assert response.status_code==200 and response.json()['role']=='ingestion' and response.headers['X-Trace-ID']
        assert client.get('/').status_code==200
        bad=client.post('/api/agents/ingestion',json={'instruction':'状态','document_id':'doc1','role':'knowledge'})
        assert bad.status_code==422
