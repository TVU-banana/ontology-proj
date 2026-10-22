"""Real CLI subprocesses; missing model configuration, no Docling conversion."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from ontology_proj.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    fields = set(Settings.model_fields)
    environment = {k:v for k,v in os.environ.items() if k.lower() not in fields}
    results = []
    checks = {}
    with tempfile.TemporaryDirectory(prefix='ontology-t05-') as temporary:
        directory = Path(temporary)
        def run(label, command, code=0):
            p = subprocess.run([sys.executable, '-m', 'ontology_proj.local_cli', '--data-root', str(directory/'data'), *command], env=environment, capture_output=True, text=True, timeout=20)
            assert p.returncode == code, (label, p.returncode)
            assert not (p.stdout if code else p.stderr), label
            value = json.loads(p.stderr if code else p.stdout)
            results.append({'case':label, 'exit_code':p.returncode, 'output':value})
            return value
        pdf = ROOT/'samples/synthetic/bridge-demo-001/input/bridge-demo-001.pdf'
        prepare = ['prepare', '--pdf', str(pdf), '--kind', 'inspection_report', '--source-kind', 'synthetic', '--bridge-id', 'B-DEMO-001']
        saved = run('prepare', prepare)
        docid = saved['source']['document_id']
        checks['prepare_is_local_only'] = saved['scope'] == 'local_parse' and not saved['duplicate']
        checks['duplicate_reused'] = run('duplicate',prepare)['duplicate']
        conflict = prepare.copy();conflict[-1] = 'B-OTHER'
        checks['context_conflict'] = run('conflict',conflict,1)['error']['code'] == 'SOURCE_CONTEXT_CONFLICT'
        checks['not_generated'] = run('read_not_generated',['read','--document-id',docid],1)['error']['code'] == 'ARTIFACT_NOT_GENERATED'
        failed = run('parse_unconfigured',['parse','--document-id',docid],1)
        checks['missing_models_fail_explicitly'] = failed['operation']['status'] == 'failed' and failed['operation']['error_code'] == 'PARSER_NOT_CONFIGURED'
        checks['retry_required'] = run('retry_required',['parse','--document-id',docid],1)['error']['code'] == 'EXPLICIT_RETRY_REQUIRED'
        retried = run('explicit_retry',['parse','--document-id',docid,'--retry'],1)
        checks['explicit_retry_new_operation'] = retried['operation_id'] != failed['operation_id'] and retried['operation']['error_code'] == 'PARSER_NOT_CONFIGURED'
        checks['failure_never_publishes'] = not (directory/'data'/'documents'/docid/'manifest.json').exists()
        checks['missing_input_sanitized'] = run('missing_input',['prepare','--pdf',str(directory/'SECRET.pdf'),'--kind','other','--source-kind','real'],1)['error']['code'] == 'INPUT_UNAVAILABLE'
        bad = directory/'bad.env';bad.write_text('APP_PORT=SECRET\nLLM_API_KEY=TOP-SECRET\n')
        checks['bad_config_sanitized'] = run('bad_config',['--env-file',str(bad),'read','--document-id',docid],2)['error']['code'] == 'INVALID_CONFIGURATION'
        checks['argument_sanitized'] = run('invalid_argument',['read','--document-id',docid,'--artifact','SECRET'],2)['error']['code'] == 'INVALID_ARGUMENT'
        # Independent root and explicit env file prove argument override and unknown bridge support.
        env = directory/'settings.env';env.write_text('DATA_ROOT='+str(directory/'ignored')+'\n')
        other = run('env_and_unknown_bridge',['--env-file',str(env),'prepare','--pdf',str(pdf),'--kind','other','--source-kind','real'],1)
        checks['override_uses_same_root'] = other['error']['code'] == 'SOURCE_CONTEXT_CONFLICT' and not (directory/'ignored').exists()
        unknown_pdf = directory/'different.pdf';unknown_pdf.write_bytes(pdf.read_bytes()+b'\n')
        unknown = run('unknown_bridge',['prepare','--pdf',str(unknown_pdf),'--kind','other','--source-kind','real'])
        checks['unknown_bridge_is_null'] = unknown['source']['context']['bridge_id'] is None
        checks['errors_do_not_leak'] = all('SECRET' not in json.dumps(r) and temporary not in json.dumps(r) for r in results)
    result={'task':'S2.1-T05','passed':all(checks.values()),'checks':checks,'cases':results,
            'boundary':'Actual production CLI subprocesses and local filesystem; parser not configured, no Docling conversion/model API/graph. Successful fixture conversion is unit-test evidence only. Temporary files cleaned.'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'passed':result['passed'],'checks':len(checks),'cases':len(results)}))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
