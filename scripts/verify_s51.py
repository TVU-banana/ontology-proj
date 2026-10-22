"""New S5.1 TCP/real-role validation; reuses existing registered publications."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import uuid
import httpx


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
    target=Path(args.output)
    if target.exists():raise RuntimeError('Evidence exists; choose a fresh output')
    root=Path('data/s23-runtime')
    sources=[]
    for path in (root/'sources').glob('*/source.json'):
        item=json.loads(path.read_text());sources.append(item)
    if not sources:
        for path in root.rglob('source.json'):sources.append(json.loads(path.read_text()))
    source=next(s for s in sources if s.get('original_filename')=='bridge-demo-001.pdf')
    did=source['document_id'];bridge=source.get('bridge_id') or 'B-DEMO-001'
    results={'document_id':did,'bridge_id':bridge,'url':args.url,'checks':[],'responses':{}}
    def check(name,ok):results['checks'].append({'name':name,'passed':bool(ok)})
    with httpx.Client(base_url=args.url,timeout=420) as client:
        def post(path,body,name):
            response=client.post(path,json=body);data=response.json();results['responses'][name]={'http_status':response.status_code,'body':data};check(name+' HTTP',response.status_code==200);return data
        scope={'document_ids':[did]}
        # Native tool invocation is part of this new step, not replay of extraction experiments.
        ingestion=post('/api/agents/ingestion',{'document_id':did,'instruction':'只调用一次get_ingestion_result读取已登记资料的处理结果，已完成无需重新处理，随后结束。'},'ingestion_agent')
        check('ingestion real tool',ingestion.get('status')=='completed' and any(e['tool_name']=='get_ingestion_result' and e['result']['ok'] and e['result']['data']['document_id']==did for e in ingestion.get('events',[])))
        knowledge=post('/api/agents/knowledge',{'scope':scope,'instruction':'请使用graph_lookup的bridge_components查询当前资料的构件。只查询构件，不问答、不生成报告，调用一次后结束。'},'knowledge_agent')
        events=knowledge.get('events',[])
        check('knowledge real tool and independent two components',knowledge.get('status')=='completed' and any(e['tool_name']=='graph_lookup' and e['result']['ok'] and len(e['result']['data']['records'])==2 for e in events))
        comps=post('/api/graph/lookup',{'query_type':'bridge_components','scope':scope},'components')['records']
        ids=[r['record_id'] for r in comps]
        observations=post('/api/graph/lookup',{'query_type':'component_observations','scope':{**scope,'component_ids':ids}},'observations')['records']
        check('independent areas and unknown dates',len(observations)==3 and sorted(r['area_value'] for r in observations)==['0.04','0.9','2.6'] and all(r['observed_at'] is None for r in observations))
        results['component_ids']=ids;results['evidence_id']=observations[0]['evidence_ids'][0]
    target.write_text(json.dumps(results,ensure_ascii=False,indent=2))
    print(json.dumps({'output':str(target),'checks':results['checks']},ensure_ascii=False))


if __name__=='__main__':main()
