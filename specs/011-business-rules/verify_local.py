"""Isolated synthetic structured-source verification; providers/graph are doubles."""
import copy
import hashlib
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'specs/011-business-rules/results'
DATA=ROOT/'data/business-rules-20261013-03'


def write(path,value):
    text=json.dumps(value,ensure_ascii=False,indent=2)+'\n'
    if path.exists():
        if path.read_text()!=text:raise ValueError('Existing verification output cannot be overwritten')
        return
    path.write_text(text)


def main():
    DATA.mkdir(exist_ok=False)
    network=[]
    def deny(*a,**kw):network.append('attempt');raise RuntimeError('Local only')
    socket.socket.connect=deny;socket.create_connection=deny;socket.getaddrinfo=deny
    sys.path.insert(0,str(ROOT/'tests/unit'))
    from test_measurement_rules import example
    from ontology_proj.adapters.business_rules import load_rules
    from ontology_proj.adapters.business_validation import BusinessValidator
    from ontology_proj.domain.graph_mapping import project_bundle
    from ontology_proj.application.query import LookupGraph
    from ontology_proj.application.answer import AnswerQuestion
    from ontology_proj.application.report import GenerateReport
    from ontology_proj.adapters.answer_journal import LocalAnswerJournal
    from ontology_proj.adapters.report_store import LocalReportStore
    rules=load_rules();validator=BusinessValidator()
    cases=[
        {'name':'cm-area','text':'面积为100平方厘米','value':'100','unit':'平方厘米','expected':'0.01','status':'accepted'},
        {'name':'mm-area','text':'面积：10000mm²','value':'10000','unit':'mm²','expected':'0.01','status':'accepted'},
        {'name':'fullwidth-area','text':'面积为１００ ｃｍ²','value':'100','unit':'ｃｍ²','expected':'0.01','status':'accepted'},
        {'name':'rectangle','text':'矩形病害，长10厘米、宽10厘米','derived':True,'expected':'0.01','status':'accepted'},
        {'name':'mixed-rectangle','text':'矩形病害，宽10厘米，长0.1米','derived':True,'expected':'0.01','status':'accepted'},
        {'name':'area-product','text':'S=2.5×0.9㎡','derived':True,'expected':'2.25','status':'accepted'},
        {'name':'length-area-formula','text':'S=10厘米×10厘米','derived':True,'expected':'0.01','status':'accepted'},
        {'name':'missing-units','text':'尺寸10×10，单位未知','derived':True,'approximate':None,'expected':None,'status':'pending_review'},
        {'name':'irregular','text':'不规则病害，长10厘米、宽10厘米','derived':True,'expected':None,'status':'pending_review'},
        {'name':'range','text':'矩形病害，长10厘米至20厘米，宽10厘米','derived':True,'expected':None,'status':'pending_review'},
        {'name':'negative','text':'未见露筋，附近修补面积100平方厘米','value':'100','unit':'平方厘米','expected':None,'status':'rejected'},
        {'name':'aggregate','text':'两处总面积100平方厘米','value':'100','unit':'平方厘米','expected':None,'status':'rejected'}]
    # These literals come from the approved plan; write before any tested computation.
    write(OUT/'local-expected.json',{'cases':cases,'origin':'approved examples and independent elementary arithmetic, not model/system output'})
    results=[];checks=[]
    def check(name,passed):checks.append({'name':name,'passed':bool(passed)})
    for case in cases:
        folder=DATA/case['name'];folder.mkdir()
        doc,candidate=example(case['text'],case.get('value'),case.get('unit'),derived=case.get('derived',False),approximate=case.get('approximate',False))
        write(folder/'source-fixture.json',doc);write(folder/'candidate-fixture.json',candidate)
        result=validator.validate(candidate,doc);write(folder/'validation.json',result)
        if result['records'] is None:
            check(case['name']+' status',case['status']=='rejected')
            results.append({'case':case['name'],'status':'rejected','diagnostics':result['diagnostics']});continue
        records=result['records'];o=records['observations'][0]
        check(case['name']+' area',o['area_value']==case['expected'])
        check(case['name']+' status',o['acceptance_status']==case['status'])
        check(case['name']+' recheck',not validator.validate_records(records,doc))
        write(folder/'records.json',records)
        projection=project_bundle(doc,records);write(folder/'graph-projection.json',projection)
        if case['status']!='accepted':
            results.append({'case':case['name'],'status':o['acceptance_status'],'value':o['area_value']});continue
        rows=[]
        for label,items in projection['nodes'].items():
            for item in items:rows.append({**item['properties'],'record_type':label,'record_id':item['id'],
                'evidence_ids':item['evidence_ids'],'source_acceptance_status':'accepted','source_document_ids':[doc['document_id']],'source_kinds':['synthetic']})
        class Repository:
            jobs={}
            def lookup(self,q):
                allowed={'bridge_components':'Component','component_observations':'Observation','defect_history':'Observation'}.get(q['query_type'])
                return copy.deepcopy([r for r in rows if (r['record_type']==allowed if allowed else r['record_id']==q['record_id'])])
            def create(self,rid,op,request):self.jobs[rid]={'status':'queued','request':request,'report_id':rid,'operation_id':op}
            def get(self,rid):return copy.deepcopy(self.jobs[rid])
            def advance(self,rid):self.jobs[rid]['status']='running'
            def fail(self,rid,code):self.jobs[rid].update(status='failed',error_code=code)
            def recover(self):pass
            def documents(self,scope):return [{'document_id':doc['document_id'],'original_filename':'source-fixture.json','source_kind':'synthetic','processing_status':'graph_ready'}]
            def statements(self,*a):return []
            def commit(self,report,snapshots,manifest):self.jobs[report['report_id']]['status']='succeeded'
        repo=Repository();lookup=LookupGraph(repo,rules)
        class Evidence:
            def document(self,*a):return None
            def read(self,eid):
                e=next(e for e in records['evidence'] if e['evidence_id']==eid)
                related=[{'record_id':r['record_id'],'acceptance_status':'accepted','source_acceptance_status':'accepted','review_status':'unreviewed'} for r in rows if eid in r['evidence_ids']]
                return {'evidence':copy.deepcopy(e),'document':{'document_id':doc['document_id'],'filename':'source-fixture.json','source_kind':'synthetic'},'records':related}
        evidence=Evidence()
        selected=lookup.execute({'query_type':'component_observations','scope':{'bridge_id':'B-TEST','component_ids':[o['component_id']]},'area_min':case['expected'],'area_max':case['expected']})['records']
        check(case['name']+' query',len(selected)==1)
        class Generator:
            def request(self,question,context):return {'fixture':'answer proposal double'}
            def generate(self,request):
                needed={'area_expression'} if o['area_expression'] else {'area_original_value','area_original_unit'}
                ids=[e['evidence_id'] for e in records['evidence'] if e['target_field'] in needed]
                return {'candidate':{'status':'answered','reason_codes':[],'claims':[{'kind':'fact','record_ids':[o['observation_id']],
                    'evidence_ids':ids,'assertions':[{'record_id':o['observation_id'],'field':'area_value','value':case['expected'],'unit':'m²'}]}]},'response':{'fixture':True}}
        context=SimpleNamespace(execute=lambda q:{'status':'ready','facts':selected,'source_statements':[],'business_passages':[],'warnings':[]})
        answer=AnswerQuestion(context,lookup,evidence,Generator(),LocalAnswerJournal(folder),rules).execute({'question':'面积是多少','scope':{'bridge_id':'B-TEST'}})
        check(case['name']+' answer',answer['status']=='answered' and case['expected']+' m²' in answer['claims'][0]['text'])
        store=LocalReportStore(folder);service=GenerateReport(lookup,evidence,repo,store,rules)
        rid=service.submit({'bridge_id':'B-TEST'})['report_id'];service.run(rid)
        check(case['name']+' report succeeded',repo.get(rid)['status']=='succeeded')
        report,markdown,manifest=store.read(rid)
        check(case['name']+' report value',report['sections'][2]['rows'][0]['values']['area_value']==case['expected'])
        check(case['name']+' report revision',report['template_version']=='1.2')
        if o['area_basis']=='rectangle_dimensions':check(case['name']+' derived label','尺寸推算' in markdown and '尺寸推算' in answer['claims'][0]['text'])
        if o['area_basis']=='explicit_area_product':check(case['name']+' formula label','原文面积式计算' in markdown and '原文面积式计算' in answer['claims'][0]['text'])
        results.append({'case':case['name'],'status':'accepted','value':o['area_value'],'basis':o['area_basis'],
            'answer_id':answer['question_id'],'report_id':rid,'report_json':str((folder/'reports'/rid/'report.json').relative_to(ROOT)),
            'report_markdown':str((folder/'reports'/rid/'report.md').relative_to(ROOT))})
    check('no network',not network)
    receipt={'passed':sum(c['passed'] for c in checks),'failed':sum(not c['passed'] for c in checks),'checks':checks,'cases':results,
        'real_execution':['rules arithmetic','candidate/formal validation','graph scalar projection/decoding','query/answer verification','answer/report file stores'],
        'doubles':['synthetic structured source/candidate','graph repository','answer generator','retrieval context'],
        'not_run':['new real model requests','new Docling conversions','Neo4j writes','professional review','live browser acceptance'],
        'network_attempts':network,'rules_version':rules.version,'rules_sha256':rules.sha256}
    write(OUT/'local-verification.json',receipt)
    print(json.dumps({'passed':receipt['passed'],'failed':receipt['failed'],'cases':len(results)}))
    return 0 if not receipt['failed'] else 1


if __name__=='__main__':raise SystemExit(main())
