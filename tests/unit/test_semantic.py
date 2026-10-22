import copy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from ontology_proj.application.semantic import build_chunks, validate_vectors, search_request, SemanticSearch, BuildContext, IndexDocument
from ontology_proj.application.files import FileStoreError
from ontology_proj.adapters.ollama_embedding import OllamaEmbedding

BASE=Path(__file__).resolve().parents[2]/'specs/002-ingestion-foundation/results/s2.2/baseline-1.1.4'
@pytest.fixture
def inputs():
    return (json.loads((BASE/'baseline-normalized.json').read_text()),json.loads((BASE/'results/baseline-01/records.json').read_text()))

@pytest.mark.parametrize('vectors,count,dim', [([],1,2),([[0,0]],1,2),([[True,1]],1,2),([[float('nan'),1]],1,2),([[float('inf'),1]],1,2),([[1]],1,2),([[1,2],[1,2]],1,2)])
def test_invalid_vectors(vectors,count,dim):
    with pytest.raises(FileStoreError):validate_vectors(vectors,count,dim)

def test_chunks_preserve_spans_and_separate_pending(inputs):
    doc,records=inputs;records['observations'][1]['acceptance_status']='pending_review'
    chunks,excluded=build_chunks(doc,records,'profile')
    blocks={b['block_id']:b for b in doc['blocks']}
    assert chunks==build_chunks(doc,records,'profile')[0]
    assert not any(records['observations'][1]['observation_id'] in c['record_ids'] for c in chunks)
    assert any(c['semantic_kind']=='negative' and c['content_kind']=='source_statement' for c in chunks)
    for c in chunks:
        assert len(c['text_for_embedding'])<=1200
        for s in c['source_spans']:assert blocks[s['block_id']]['text'][slice(*s['charspan'])]==s['quote']
    assert any(e['reason']=='NOT_ACCEPTED' for e in excluded)
    assert any(e['reason']=='UNSELECTED_BLOCK' for e in excluded)

def test_mismatch_rejected(inputs):
    doc,records=inputs;records['evidence'][0]['quote']='does not occur'
    with pytest.raises(FileStoreError,match='Chunk source'):build_chunks(doc,records,'profile')

def test_window_geometry_and_missing_location(inputs):
    doc,records=inputs;block=doc['blocks'][0];block['text']='甲'*2400
    block['locations'][0]['charspan']=[0,2400]
    records={k:[] for k in ('bridges','components','defects','observations','evidence')}
    records['statements']=[dict(kind='unmapped',sources=[dict(block_id=block['block_id'],exact_quote=block['text'])])]
    chunks,_=build_chunks(doc,records,'profile');assert len(chunks)==3
    assert chunks[1]['source_spans'][0]['charspan'][0]==950
    block['locations']=[];assert not build_chunks(doc,records,'profile')[0]

@pytest.mark.parametrize('change',[{'scope':{}},{'top_k':True},{'top_k':21},{'query':' '},{'cypher':'RETURN 1'}])
def test_requests(change):
    with pytest.raises(FileStoreError):search_request(dict(query='text',scope={'document_ids':['doc']},**{} )|change)

class Provider:
    def __init__(self,fail=False):self.calls=0;self.fail=fail
    def embed(self,texts,query=False):
        self.calls+=1
        if self.fail:raise FileStoreError('EMBEDDING_UNAVAILABLE','fixture')
        return [[1,0] for _ in texts]

class Repository:
    def __init__(self,rows=None):self.rows=rows or [];self.documents=[dict(document_id='doc',bridge_id='b',index_status='ready',index_profile='p')]
    def allowed_documents(self,scope):return self.documents
    def search(self,*args):return self.rows

def chunk(**changes):
    return dict(chunk_id='c',document_id='doc',bridge_id='b',source_kind='synthetic',component_ids=['component'],observed_at=None,record_type='Observation',record_ids=['o'],content_kind='business_fact',score=.9,current_records=[dict(acceptance_status='accepted',source_acceptance_status='accepted',review_status='unreviewed')],embedding=[1,0])|changes

def run(rows,scope=None):
    return SemanticSearch(Repository(rows),Provider(),'p',2).execute(dict(query='text',scope={'document_ids':['doc']}|(scope or {})))

@pytest.mark.parametrize('scope',[{'include_synthetic':False},{'bridge_id':'wrong'},{'component_ids':['wrong']},{'date_from':'2026-01-01'},{'review_mode':'confirmed_only'}])
def test_scope_no_refill(scope):assert run([chunk(),chunk(chunk_id='outside',document_id='outside')],scope)['chunks']==[]

def test_current_source_status():
    assert not run([chunk(current_records=[dict(acceptance_status='accepted',source_acceptance_status='pending_review')])])['chunks']
    assert not run([chunk(current_records=[])])['chunks']
    assert 'embedding' not in run([chunk()])['chunks'][0]


def test_unassigned_bridge_chunk_visibility_regression():
    c = chunk(bridge_id=None)
    res = run([c], {'bridge_id': None})
    assert len(res['chunks']) == 1
    assert res['chunks'][0]['chunk_id'] == 'c'

def test_multiple_bridge_clarify_and_no_model():
    repo=Repository();repo.documents.append(dict(document_id='other',bridge_id='other'))
    provider=Provider();assert SemanticSearch(repo,provider,'p',2).execute(dict(query='q',scope={'document_ids':['doc','other']}))['status']=='needs_clarification'
    assert provider.calls==0

def test_degraded_context_preserves_graph_and_budget():
    search=SemanticSearch(Repository(),Provider(True),'p',2)
    lookup=SimpleNamespace(execute=lambda r:dict(records=[dict(record_id='o',evidence_ids=['e'],text='x'*100),dict(record_id='large',text='x'*8100)],warnings=[]))
    context=BuildContext(search,lookup).execute(dict(query='q',scope={'document_ids':['doc']},graph_query={'query_type':'record_by_id','record_id':'o'}))
    assert context['status']=='degraded' and len(context['facts'])==1 and context['content_characters']<=8000
    assert context['business_passages']==[] and context['warnings']
    with pytest.raises(FileStoreError):BuildContext(search,lookup).execute(dict(query='q',scope={'document_ids':['doc']},graph_query={'scope':{}}))

def test_index_reuse_and_failure(inputs):
    provider=Provider();repo=SimpleNamespace(begin=lambda *a:dict(reused=True))
    assert IndexDocument(None,repo,provider,'p',2).execute('doc')['reused'] and provider.calls==0
    failed=[];repo.begin=lambda *a:dict(reused=False,run_id='r');repo.fail=lambda *a:failed.append(a)
    index=IndexDocument(SimpleNamespace(read=lambda _:inputs),repo,Provider(True),'p',2)
    with pytest.raises(FileStoreError):index.execute('doc')
    assert failed==[('doc','r','EMBEDDING_UNAVAILABLE')]

def test_ollama_payload_and_identity(monkeypatch):
    provider=OllamaEmbedding('http://127.0.0.1:11434','m','digest',2)
    calls=[]
    def call(path,body=None):
        calls.append((path,body))
        return {'models':[{'name':'m','digest':'digest'}]} if path=='/api/tags' else {'model':'m','embeddings':[[3,4]]}
    monkeypatch.setattr(provider,'_call',call)
    assert provider.embed(['q'],query=True)==[[.6,.8]]
    body=calls[1][1];assert body['truncate'] is False and body['input'][0].startswith('Instruct:')
    monkeypatch.setattr(provider,'_call',lambda *a:{'models':[{'name':'m','digest':'changed'}]})
    with pytest.raises(FileStoreError) as e:provider.embed(['q'])
    assert e.value.code=='EMBEDDING_MODEL_CHANGED' and provider.calls==1

@pytest.mark.parametrize('metadata',[[],{}, {'models':None},{'models':['bad']}])
def test_malformed_metadata(monkeypatch,metadata):
    provider=OllamaEmbedding('http://127.0.0.1:11434','m','digest',2)
    monkeypatch.setattr(provider,'_call',lambda *a:metadata)
    with pytest.raises(FileStoreError) as e:provider.embed(['q'])
    assert e.value.code=='EMBEDDING_INVALID' and provider.calls==0

def test_invalid_stored_date_degrades():
    assert run([chunk(observed_at='2026-99-99')],{'date_from':'2026-01-01'})['error_code']=='GRAPH_RECORD_INVALID'

def test_http_contract_and_errors():
    from fastapi.testclient import TestClient
    from ontology_proj.interfaces.http.app import create_http_app
    search=SemanticSearch(Repository([chunk()]),Provider(),'p',2)
    indexer=SimpleNamespace(execute=lambda *a:{'index_status':'ready','reused':True})
    app=create_http_app(None,semantic=search,indexer=indexer,context=BuildContext(search,None))
    with TestClient(app) as client:
        body=dict(query='q',scope={'document_ids':['doc']})
        assert client.post('/api/semantic/search',json=body).json()['chunks']
        assert client.post('/api/context',json=body).json()['business_passages']
        assert client.post('/api/documents/doc/index',json={}).json()['reused']
        for endpoint,b in [('/api/semantic/search',body|{'graph_query':{}}),('/api/semantic/search',body|{'top_k':True}),('/api/documents/doc/index',{'extra':True}),('/api/context',body|{'graph_query':{'scope':{}}})]:
            response=client.post(endpoint,json=b)
            assert response.status_code==422 and response.json()['error']['code']=='INVALID_REQUEST'

def test_context_budget_retains_high_ranked_negative():
    negative=chunk(chunk_id='negative',content_kind='source_statement',semantic_kind='negative',record_ids=[],current_records=[],score=.99,text_for_embedding='否'*2000)
    business=chunk(text_for_embedding='实'*4500,score=.8)
    lookup=SimpleNamespace(execute=lambda r:dict(records=[dict(record_id='fact',text='图'*2000)],warnings=[]))
    context=BuildContext(SemanticSearch(Repository([business,negative]),Provider(),'p',2),lookup).execute(dict(query='未见病害',scope={'document_ids':['doc']},graph_query={'query_type':'bridge_components'}))
    assert context['facts'] and context['source_statements'][0]['semantic_kind']=='negative'
    assert not context['business_passages'] and context['content_characters']<=8000
