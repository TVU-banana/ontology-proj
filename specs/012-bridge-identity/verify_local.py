"""Isolated structured-source fixtures; real catalog/extraction validation/publication.
No PDF parser, external generation or graph server; graph transaction is a double.
"""
import hashlib
import json
from pathlib import Path
import socket
import sys

ROOT=Path(__file__).resolve().parents[2]
DATA=ROOT/'data/bridge-identity-20261013-02'
RESULTS=ROOT/'specs/012-bridge-identity/results'


def main():
    DATA.mkdir(exist_ok=False)
    network=[]
    def deny(*args,**kwargs):network.append('attempt');raise RuntimeError('Offline verification')
    socket.socket.connect=deny;socket.create_connection=deny;socket.getaddrinfo=deny
    sys.path.insert(0,str(ROOT/'tests/unit'))
    from test_bridge_identity import business_doc,business_catalog
    from ontology_proj.adapters.business_store import LocalBusinessStore
    from ontology_proj.adapters.catalog_extractor import CatalogFactExtractor
    from ontology_proj.adapters.business_validation import BusinessValidator
    from ontology_proj.adapters.neo4j_repository import Neo4jRepository
    from ontology_proj.application.business_extraction import ExtractBusiness
    from ontology_proj.domain.graph_mapping import project_bundle
    from ontology_proj.adapters.bridge_catalog import BridgeCatalog
    catalog,master=business_catalog(DATA)
    checks=[];rows=[];nodes={};writes=[]
    def check(name,value):checks.append({'name':name,'passed':bool(value)})
    def save(path,value):
        if path.exists():raise RuntimeError('Cannot overwrite verification artifact')
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    # Explicit expected behavior saved before executing extraction or arithmetic.
    expected={'reports':3,'bridge_count':1,'original_codes':['B-TEST',None,None],
        'resolution_reasons':['official_id','name_location','user_selection'],'distinct_components':3,'normalized_area':'0.01'}
    assert json.loads((RESULTS/'local-expected.json').read_text())==expected
    class Store(LocalBusinessStore):
        def read_artifact(self,document_id,artifact):
            if artifact!='normalized':raise RuntimeError('Only fixture normalization supplied')
            return json.loads((self._directory(document_id)/'source-fixture.json').read_text())
    store=Store(DATA);repo=Neo4jRepository(None,None,None)
    class Result:
        def __init__(self,row=None):self.row=row
        def single(self):return self.row
        def consume(self):pass
    class Tx:
        def __init__(self,document):self.document=document
        def run(self,q,**kw):
            writes.append({'query':q,'parameters':kw})
            if 'AS op' in q:return Result({'op':{'status':'running','actor':'verification'},'doc':{'document_id':self.document['document_id'],'source_kind':'synthetic','processing_status':'extracting','operation_id':'local-op'}})
            if q.startswith('MATCH (n:') and 'RETURN properties(n)' in q:return Result({'value':nodes[kw['id']]}) if kw['id'] in nodes else Result()
            if q.startswith('CREATE (n:'):
                props=kw['props'];identity=next(k for k in ('bridge_id','component_id','defect_id','observation_id') if k in props and q.startswith('CREATE (n:'+{'bridge_id':'Bridge','component_id':'Component','defect_id':'Defect','observation_id':'Observation'}[k]+')'))
                nodes[props[identity]]=props
            return Result()
    for n,code in [(1,'B-TEST'),(2,None),(3,None)]:
        d,candidate=business_doc(n,code)
        if n==3:
            text=d['blocks'][0]['text'].split('\n桥梁名称：')[0]
            d['blocks'][0]['text']=text;d['blocks'][0]['locations'][0]['charspan'][1]=len(text)
            d['import_context']['bridge_id']=master['bridge_id']
        path=store._directory(d['document_id']);save(path/'source-fixture.json',d);save(path/'candidate-fixture.json',candidate)
        save(path/'manifest.json',{'fixture_parse_manifest':True,'source_fixture_sha256':hashlib.sha256((path/'source-fixture.json').read_bytes()).hexdigest()})
        binding=catalog.resolve(d,master['bridge_id'] if n==3 else None)
        class Provider(CatalogFactExtractor):
            def extract(self,request):return {'candidate':candidate,'response':{'fixture_generator':True},'diagnostics':[]}
        provider=Provider('http://offline.invalid/v1','fixture-model','not-a-real-key',catalog)
        result=ExtractBusiness(store,provider,BusinessValidator(catalog=catalog)).execute(d['document_id'])
        check(f'report {n} publication',result['status']=='succeeded')
        records=store.read_business_artifact(d['document_id'],'records')
        check(f'report {n} bridge',records['bridges'][0]['bridge_id']==master['bridge_id'])
        check(f'report {n} original code',records['bridges'][0]['bridge_code']==code)
        check(f'report {n} area',records['observations'][0]['area_value']=='0.01')
        check(f'report {n} restart',BridgeCatalog(DATA).binding(d)['bridge_id']==master['bridge_id'])
        check(f'report {n} records recheck',not BusinessValidator(catalog=BridgeCatalog(DATA)).validate_records(records,d))
        effective={**d,'bridge_binding':binding};projection=project_bundle(effective,records)
        graph=repo._commit(Tx(d),'local-op',effective,records,projection,[],'local-audit')
        check(f'report {n} graph transaction double',graph['bridge_id']==master['bridge_id'] and 'OBJECT_CONFLICT' not in graph['warnings'])
        save(path/'graph-projection.json',projection)
        # Publication is reused without calling proposal generation again.
        provider.extract=lambda *_:(_ for _ in ()).throw(RuntimeError('No repeat generation'))
        check(f'report {n} reuse',ExtractBusiness(store,provider,BusinessValidator(catalog=catalog)).execute(d['document_id'])['reused'])
        rows.append({'document_id':d['document_id'],'bridge_id':binding['bridge_id'],'reason':binding['reason'],'original_code':code,'component_id':records['components'][0]['component_id'],'publication':result})
    check('one bridge master',len(catalog.list())==1)
    check('three document-local components',len({r['component_id'] for r in rows})==3)
    check('three distinct resolution paths',[r['reason'] for r in rows]==['official_id','name_location','user_selection'])
    check('no network attempts',network==[])
    save(DATA/'graph-transaction-double.json',writes)
    proof={'passed':all(c['passed'] for c in checks),'checks':checks,'reports':rows,'data_root':str(DATA),'network_attempts':network,
        'real':['bridge catalog/locks/atomic publication','request/schema generation','business source/rule validation','LocalBusinessStore publication and hash-verified reads','graph projection and Neo4j commit code'],
        'doubles':['normalized structured source fixture, not PDF/Docling','proposal generator, not external model','graph transaction, not Neo4j server'],
        'external_model_calls':0,'embedding_calls':0,'docling_calls':0,'real_graph_writes':0}
    save(RESULTS/'local-verification.json',proof)
    assert proof['passed'],checks
    print(json.dumps({'passed':True,'checks':len(checks),'reports':3},ensure_ascii=False))


if __name__=='__main__':main()
