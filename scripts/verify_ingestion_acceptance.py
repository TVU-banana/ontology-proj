"""T06 real production CLI/Docling acceptance; golden facts only read afterwards."""
import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from ontology_proj.adapters.docling_parser import REQUIRED_MODEL_FILES
from ontology_proj.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / 'samples/synthetic/bridge-demo-001/input/bridge-demo-001.pdf'


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('--layout-model-dir', type=Path, required=True)
    cli.add_argument('--table-model-dir', type=Path, required=True)
    cli.add_argument('--data-root', type=Path, required=True)
    cli.add_argument('--output-dir', type=Path, required=True)
    args = cli.parse_args()
    data, out = args.data_root.resolve(), args.output_dir.resolve()
    if data.exists() or out.exists():
        cli.error('Use fresh directories; first results must not be overwritten')
    out.mkdir(parents=True)
    fields = set(Settings.model_fields)
    env = {k: v for k, v in os.environ.items() if k.lower() not in fields}
    env.update(HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', DOCLING_NUM_THREADS='4')
    cases, checks = [], {}
    prepare = ['prepare', '--pdf', str(PDF), '--kind', 'inspection_report', '--source-kind', 'synthetic', '--bridge-id', 'B-DEMO-001']
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix='ontology-t06-') as temporary:
        scratch = Path(temporary)
        models = scratch / 'models'
        models.mkdir()
        (models / 'docling-project--docling-layout-heron').symlink_to(args.layout_model_dir.resolve(), target_is_directory=True)
        (models / 'docling-project--docling-models').symlink_to(args.table_model_dir.resolve(), target_is_directory=True)
        env['DOCLING_ARTIFACTS_PATH'] = str(models)
        model_files = {k: {'sha256': digest(models/k), 'bytes': (models/k).stat().st_size} for k in REQUIRED_MODEL_FILES}

        def command(root, arguments):
            return [sys.executable, '-m', 'ontology_proj.local_cli', '--data-root', str(root), *arguments]

        def run(label, arguments, *, root=data, expected=0):
            begin = time.perf_counter()
            result = subprocess.run(command(root, arguments), env=env, capture_output=True, text=True, timeout=120)
            # Retain the first stdout/stderr even if acceptance fails.
            (out / (label + '-stdout.json')).write_text(result.stdout)
            (out / (label + '-stderr.log')).write_text(result.stderr)
            payload = json.loads(result.stdout if result.returncode == 0 else result.stderr)
            cases.append({'case': label, 'exit_code': result.returncode, 'elapsed_seconds': round(time.perf_counter()-begin, 3), 'output': payload})
            write(out/'runtime.json', {'cases': cases, 'status': 'in_progress', 'model_files': model_files})
            assert result.returncode == expected, label
            return payload

        saved = run('prepare', prepare)
        docid = saved['source']['document_id']
        directory = data / 'documents' / docid
        checks['P01_source_bytes_hash'] = (directory/'source.pdf').read_bytes() == PDF.read_bytes() and docid == 'doc_' + digest(PDF)
        parsed = run('parse', ['parse', '--document-id', docid])
        checks['P02_real_conversion_published'] = parsed['operation']['status'] in ('succeeded', 'succeeded_with_warnings') and (directory/'manifest.json').is_file()
        manifest = run('restart-read', ['read', '--document-id', docid])
        native = run('read-native', ['read', '--document-id', docid, '--artifact', 'docling'])
        normalized = run('read-normalized', ['read', '--document-id', docid, '--artifact', 'normalized'])
        for name in ['source.json', 'docling.json', 'normalized.json', 'manifest.json']:
            shutil.copyfile(directory/name, out/name)
        shutil.copyfile(data/'operations'/parsed['operation_id']/'diagnostic.json', out/'diagnostic.json')
        baseline = {p.name: digest(p) for p in directory.iterdir() if p.is_file()}
        checks['P01_duplicate_prepare'] = run('duplicate', prepare)['duplicate']
        reused = run('duplicate-parse', ['parse', '--document-id', docid])
        checks['P01_duplicate_no_new_operation'] = reused['reused'] and reused['operation_id'] == parsed['operation_id'] and len(list((data/'operations').iterdir())) == 1
        conflict = prepare.copy(); conflict[-1] = 'B-OTHER'
        checks['P01_context_conflict'] = run('conflict', conflict, expected=1)['error']['code'] == 'SOURCE_CONTEXT_CONFLICT'
        checks['P01_conflict_preserves_files'] = baseline == {p.name: digest(p) for p in directory.iterdir() if p.is_file()}
        checks['P06_completed_restart_reads_same'] = manifest == json.loads((directory/'manifest.json').read_text())
        checks['P02_manifest_hashes'] = all(digest(directory/name) == entry['sha256'] and (directory/name).stat().st_size == entry['bytes'] for name, entry in manifest['artifacts'].items())
        checks['P02_fixed_configuration'] = manifest['configuration']['device'] == 'cpu' and manifest['configuration']['num_threads'] == 4 and not manifest['configuration']['do_ocr'] and manifest['configuration']['do_table_structure']

        # Independent expectations are never inputs to the conversion process.
        golden = json.loads((ROOT/'samples/synthetic/bridge-demo-001/golden.json').read_text())
        blocks = normalized['blocks']
        text = '\n'.join(b['text'] for b in blocks)
        facts = golden['positive_facts'] + golden['negative_facts']
        checks['P03_fact_quotes_on_expected_pages'] = all(any(f['target_text'] in b['text'] and any(loc['page_no'] == f['page_no'] for loc in b['locations']) for b in blocks) for f in facts)
        checks['P03_unknown_date_text'] = '所有观测日期均未知。' in text and '表中两条观测的日期均未知。' in text
        checks['P03_four_tables'] = len(normalized['tables']) == len(native['tables']) == 4
        checks['P03_table_native_data'] = [t['native_data'] for t in normalized['tables']] == [t['data'] for t in native['tables']]
        checks['P03_explicit_synthetic_no_entities'] = normalized['source_kind'] == 'synthetic' and 'entities' not in normalized and 'records' not in normalized
        checks['P03_contiguous_unique_ids'] = [b['block_id'] for b in blocks] == [f'{docid}:block:{i:06d}' for i in range(1, len(blocks)+1)]
        def resolve(ref):
            value = native
            for token in ref[2:].split('/'):
                value = value[int(token)] if isinstance(value, list) else value[token]
            return value
        checks['P03_native_quote_reference'] = all(b['text'] == resolve(b['source_ref'])['text'] for b in blocks)
        checks['P04_bbox_span_valid'] = all(loc['page_no'] in (1, 2) and len(loc['bbox']) == 4 and all(math.isfinite(x) and 0 <= x <= 1 for x in loc['bbox']) and loc['bbox'][0] < loc['bbox'][2] and loc['bbox'][1] < loc['bbox'][3] and 0 <= loc['charspan'][0] <= loc['charspan'][1] <= len(b['text']) for b in blocks for loc in b['locations'])
        checks['P04_all_blocks_located'] = all(b['locations'] and b['location_status'] == 'available' for b in blocks)
        checks['P04_two_unrotated_pages'] = len(normalized['pages']) == 2 and all(p['rotation'] == 0 for p in normalized['pages'])

        for label, action, code in [('missing', 'missing', 'ARTIFACT_MISSING'), ('tampered', 'tampered', 'ARTIFACT_MISMATCH')]:
            copied = scratch/label
            shutil.copytree(data, copied)
            target = copied/'documents'/docid/'normalized.json'
            if action == 'missing': target.unlink()
            else: target.write_text('{}')
            checks['P05_'+label] = run(label, ['read', '--document-id', docid], root=copied, expected=1)['error']['code'] == code
        blocked = scratch/'blocked'; blocked.write_text('not a directory')
        checks['P05_actual_storage_failure'] = run('storage-failure', prepare, root=blocked, expected=1)['error']['code'] == 'UNSAFE_STORAGE_PATH'
        broken_pdf = scratch/'broken.pdf'; broken_pdf.write_bytes(b'%PDF-1.7\ntruncated invalid PDF\n')
        broken_prepare = prepare.copy(); broken_prepare[2] = str(broken_pdf)
        broken_root = scratch/'broken'
        broken_id = run('broken-prepare', broken_prepare, root=broken_root)['source']['document_id']
        broken = run('broken-parse', ['parse', '--document-id', broken_id], root=broken_root, expected=1)
        checks['P05_real_corrupt_pdf'] = broken['operation']['status'] == 'failed' and broken['operation']['error_code'] == 'PARSER_CONVERSION_FAILED'
        checks['P05_corrupt_never_published'] = not (broken_root/'documents'/broken_id/'manifest.json').exists()

        interrupted = scratch/'interrupted'
        run('interrupt-prepare', prepare, root=interrupted)
        log_path = out/'interrupted-process.log'
        with log_path.open('w') as log:
            process = subprocess.Popen(command(interrupted, ['parse', '--document-id', docid]), env=env, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 20
                while not list((interrupted/'operations').glob('*/diagnostic.json')):
                    if process.poll() is not None or time.monotonic() >= deadline: raise RuntimeError('Parsing stage not reached')
                    time.sleep(.05)
                time.sleep(2)
                assert process.poll() is None
                process.terminate(); process.wait(timeout=10)
            finally:
                if process.poll() is None: process.kill(); process.wait()
        old_ops = list((interrupted/'operations').glob('*/diagnostic.json'))
        before = [json.loads(p.read_text()) for p in old_ops]
        run('interrupt-restart-read', ['read', '--document-id', docid], root=interrupted, expected=1)
        after = [json.loads(p.read_text()) for p in old_ops]
        write(out/'interruption.json', {'before': before, 'after': after, 'phase': 'Persisted parsing stage, during startup/model initialization; not proven mid-page conversion', 'process_stopped': process.poll() is not None})
        checks['P06_interrupted_on_restart'] = len(after) == 1 and before[0]['status'] == 'running' and after[0]['status'] == 'interrupted'
        checks['P06_no_auto_resume'] = len(list((interrupted/'operations').iterdir())) == 1 and not (interrupted/'documents'/docid/'manifest.json').exists()
        checks['P02_original_snapshot_unchanged'] = baseline == {p.name: digest(p) for p in directory.iterdir() if p.is_file()}
        result = {'stage': 'S2.1-T06', 'status': 'passed' if all(checks.values()) else 'failed', 'cases': cases, 'model_files': model_files, 'configuration': manifest['configuration'], 'versions': manifest['versions'], 'elapsed_seconds': round(time.perf_counter()-started, 3), 'source_sha256': digest(PDF), 'boundary': 'Real production CLI and local Docling; HF offline environment, no business model API or graph. Interruption during startup/initialization. Temp model links and destructive case roots cleaned; successful DATA_ROOT retained.'}
        write(out/'runtime.json', result)
        write(out/'checks.json', {'passed': all(checks.values()), 'checks': checks, 'blocks': len(blocks), 'located_blocks': sum(bool(b['locations']) for b in blocks), 'tables': len(normalized['tables']), 'pages': len(normalized['pages']), 'warnings': dict(Counter(w['code'] for w in normalized['warnings'])), 'visual_review': 'pending', 'golden_sha256': digest(ROOT/'samples/synthetic/bridge-demo-001/golden.json')})
        print(json.dumps({'status': result['status'], 'checks': len(checks), 'blocks': len(blocks), 'elapsed_seconds': result['elapsed_seconds']}))
        raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == '__main__':
    main()
