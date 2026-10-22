"""Ten independent production CLI calls against a pre-frozen business suite."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from business_stability_compare import compare
from ontology_proj.adapters.fact_extractor import canonical

ROOT=Path(__file__).resolve().parents[1]


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path,value): path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--suite',type=Path,required=True)
    parser.add_argument('--env-file',type=Path,required=True)
    args=parser.parse_args()
    suite=args.suite.resolve();freeze=json.loads((suite/'freeze.json').read_text());data=ROOT/freeze['data_root']
    output=suite/'results'
    if output.exists(): parser.error('First-pass results already exist; no overwrite or automatic resume')
    def verify():
        assert all(sha(suite/p)==h for p,h in freeze['frozen_artifacts'].items()),'Frozen suite changed'
        assert all(sha(ROOT/p)==h for p,h in freeze['frozen_code'].items()),'Frozen code changed'
        assert all(sha(data/p)==h for p,h in freeze['immutable_parse_artifacts'].items()),'Parse inputs changed'
    verify();output.mkdir()
    expected=json.loads((suite/'expectations-1.1.json').read_text());case_expectations={c['case_id']:c['expected'] for c in expected['cases']}
    # Expected facts stay exclusively in this post-call process.
    summaries=[]
    environment=dict(os.environ)
    for case in freeze['cases']:
        document=json.loads((suite/case['normalized']).read_text())
        request=json.loads((suite/case['request']).read_text())
        for repetition in [1,2]:
            verify();folder=output/(case['case_id']+f'-{repetition:02d}');folder.mkdir()
            started=time.monotonic()
            result=subprocess.run([sys.executable,'-m','ontology_proj.local_cli','--env-file',str(args.env_file),'--data-root',str(data),'extract','--document-id',case['document_id'],'--fresh'],capture_output=True,text=True,env=environment,timeout=200)
            (folder/'stdout.json').write_text(result.stdout);(folder/'stderr.json').write_text(result.stderr)
            payload=json.loads(result.stdout if result.returncode==0 else result.stderr)
            run_id=payload.get('run_id')
            outcome={'case_id':case['case_id'],'repetition':repetition,'exit_code':result.returncode,'elapsed_seconds':round(time.monotonic()-started,3),'run':payload,'passed':False}
            if run_id:
                run=data/'documents'/case['document_id']/'business-runs'/run_id
                for p in run.glob('*.json'): (folder/p.name).write_bytes(p.read_bytes())
                actual_request=json.loads((run/'request.json').read_text())
                outcome['frozen_request_matches']=canonical(actual_request)==canonical(request)
                if payload['status']=='succeeded':
                    records=json.loads((run/'records.json').read_text())
                    evaluation=compare(records,document,case_expectations[case['case_id']],expected['common'])
                    write(folder/'evaluation.json',evaluation)
                    outcome['passed']=evaluation['failed']==0 and outcome['frozen_request_matches']
                    outcome['failed_fact_checks']=evaluation['failed']
                    outcome['counts']=evaluation['counts']
                else:
                    validation=run/'validation.json'
                    if validation.exists():outcome['diagnostics']=json.loads(validation.read_text())['diagnostics']
            write(folder/'summary.json',outcome);summaries.append(outcome)
            passed=sum(s['passed'] for s in summaries)
            write(output/'summary.json',{'planned':10,'executed':len(summaries),'first_pass_passed':passed,'stage_passed':len(summaries)==10 and passed==10,'calls':summaries})
            print(json.dumps({'case':case['case_id'],'repetition':repetition,'status':payload.get('status','failed'),'fact_passed':outcome['passed'],'progress':f'{len(summaries)}/10'}),flush=True)
    verify()
    raise SystemExit(0 if all(s['passed'] for s in summaries) else 1)


if __name__=='__main__': main()
