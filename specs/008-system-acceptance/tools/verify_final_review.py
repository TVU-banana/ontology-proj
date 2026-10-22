"""Read-only recheck of saved real outputs; never starts services/providers."""
import hashlib,json,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
DATA=ROOT/'data/s52-real-shengli-revalidated-20261012-01'
PRIOR=ROOT/'specs/008-system-acceptance/results/real-shengli-revalidated-20261012-01'
OUT=ROOT/'specs/008-system-acceptance/results/final-review-20261012-01/recheck-02.json'
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    if OUT.exists():raise RuntimeError('Use a fresh evidence path')
    checks=[]
    def ck(name,value):checks.append({'name':name,'passed':bool(value)})
    pdf=ROOT/'data/s52-local-shengli-20261012-01/input/shengli-original.pdf'
    ck('original PDF SHA matches confirmed source',sha(pdf)=='29fc28b28785c94a9ab96f32b2c867d88d3c1cd7aec61255a535aa7a6dc9126a')
    norm=read(ROOT/'data/s52-local-shengli-20261012-01/parsed/normalized.json');blocks={b['block_id']:b for b in norm['blocks']}
    expected=read(ROOT/'data/s52-local-shengli-20261012-01/review/confirmed-20261012-01/execution-expectations.json')
    rfile=next((DATA/'runtime').rglob('records.json'));r=read(rfile);ev={e['evidence_id']:e for e in r['evidence']}
    ck('record counts 1 bridge/3 components/3 defects/3 observations/47 evidence',[len(r[k]) for k in ['bridges','components','defects','observations','evidence']]==[1,3,3,3,47])
    for ex in expected['core_expected_values']:
        cs=[c for c in r['components'] if c['location_text']==ex['location_text']];ck(ex['fact_id']+' unique component',len(cs)==1)
        c=cs[0];ds=[d for d in r['defects'] if d['component_id']==c['component_id']];obs=[o for o in r['observations'] if o['defect_id'] in {d['defect_id'] for d in ds}]
        ck(ex['fact_id']+' one defect and observation',len(ds)==len(obs)==1);o=obs[0]
        ck(ex['fact_id']+' actual expected fields',o['area_value']==ex['area_value_m2'] and o['area_unit']=='m²' and o['observed_at'] is None and c['component_type']==ex['component_type'] and c['component_code'] is None and o['assertion']=='positive' and o['scope']=='single_defect')
    for e in ev.values():
        b=blocks[e['block_id']];lo,hi=e['charspan'];ck('original span/page/region '+e['evidence_id'],b['text'][lo:hi]==e['quote'] and any(loc['page_no']==e['page_no'] and all(abs(x-y)<0.0000011 for x,y in zip(loc['bbox'],e['bbox'])) for loc in b['locations']))
    citation_count=0
    for f in (DATA/'runtime/answers').glob('*/answer.json'):
        a=read(f);c=read(f.parent/'context.json')
        saved=next(read(p)['body'] for p in PRIOR.glob('http-answer-*.json') if read(p)['body']['question_id']==a['question_id'])
        ck('saved answer equals actual API '+a['question_id'],a=={k:v for k,v in saved.items() if k!='trace_id'})
        for claim in a['claims']:
            for eid in claim['evidence_ids']:
                citation_count+=1;v=c['evidence'][eid];matching=[b for b in blocks.values() if v['quote'] in b['text'] and any(loc['page_no']==v['page_no'] for loc in b['locations'])]
                ck('answer cited original source '+eid,bool(matching) and v['source_kind']=='real' and v['document_id']==r['document_id'])
    rid='f67b5e3b-aa8b-4f1f-8aab-f8a99904797d';rp=DATA/'runtime/reports'/rid;report=read(rp/'report.json')
    ck('report JSON content equality, copy trailing newline recorded',read(DATA/'report-reviewed.json')==report)
    ck('report Markdown copy byte equality',(DATA/'report-reviewed.md').read_bytes()==(rp/'report.md').read_bytes())
    ck('five chapters',[s['section_id'] for s in report['sections']]==['scope','sources','observations','limitations','evidence_index'])
    rows=report['sections'][2]['rows'];ck('report exactly same three observations',len(rows)==3 and {row['observation_id'] for row in rows}=={o['observation_id'] for o in r['observations']})
    for row in rows:
        o=next(o for o in r['observations'] if o['observation_id']==row['observation_id']);ck('report exact values '+o['observation_id'],all(row['values'][k]==o[k] for k in ['description','area_original_unit','area_original_value','area_unit','area_value','observed_at','assertion','scope']))
    ck('program professional placeholder unchanged',report['professional_review']=='not_run')
    ck('old independent comparison still passed',read(PRIOR/'independent-comparison.json')['passed'])
    for run in ['03','04']:
        p=ROOT/f'specs/008-system-acceptance/results/real-shengli-20261012-{run}/extraction-result.json';ck('original run'+run+' remains rejected',read(p)['status']=='rejected')
    ck('first report chain failure retained',read(PRIOR/'chain-result.json')['status']=='chain_failed')
    ck('successful continuation retained',read(PRIOR/'report-continuation.json')['status']=='succeeded')
    protected=read(DATA/'protected-source-run.json');ck('all21 original run04 files unchanged',all((ROOT/k).is_file() and sha(ROOT/k)==v for k,v in protected.items()))
    wheel=DATA/'delivery/ontology_proj-0.1.0-py3-none-any.whl';ck('same final wheel',sha(wheel)==read(PRIOR/'delivery-check.json')['wheel_sha256'])
    with zipfile.ZipFile(wheel) as z:
        src=[p for p in (ROOT/'src/ontology_proj').rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc']
        ck('current278 source/resource bytes equal installed wheel',len(src)==278 and all(z.read(str(p.relative_to(ROOT/'src')))==p.read_bytes() for p in src))
    result={'baseline':'main/91ae498','passed':all(c['passed'] for c in checks),'check_count':len(checks),'answer_citations_checked':citation_count,'checks':checks,'input_hashes':{str(p.relative_to(ROOT)):sha(p) for p in [pdf,rfile,DATA/'report-reviewed.json',DATA/'report-reviewed.md',PRIOR/'independent-comparison.json',wheel]},'new_parser_generation_embedding_calls':0,'services_started':0,'boundary':'Saved original artifacts rechecked, no new execution/stability test. Human feedback recorded separately. Existing complete-page renders visually reread by Agent.'}
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'passed':result['passed'],'checks':len(checks),'citations':citation_count,'failed':[c for c in checks if not c['passed']]}));return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
