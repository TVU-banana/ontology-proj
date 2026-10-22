"""Real process restart/termination with an explicit saved-native parser fixture."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

from ontology_proj.application.dto import ImportContext
from ontology_proj.application.parsing import ParseOutcome, ParseStatus
from ontology_proj.bootstrap import build_local_parsing
from ontology_proj.domain.source import DocumentKind, SourceKind
from ontology_proj.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


class SnapshotParser:
    def __init__(self, directory, block=False):
        self.directory, self.block, self.calls = directory, block, 0
    def configuration(self): return {'fixture': 'saved-native-parse-001'}
    def parse(self, path, document_id):
        self.calls += 1
        if self.block:
            (self.directory / 'entered.marker').write_text('fixture parser entered')
            threading.Event().wait()
        native = json.loads((ROOT / 'experiments/docling-parse-001/results/docling.json').read_text())
        return ParseOutcome(document_id, ParseStatus.SUCCESS, native, (), {'fixture': 'saved'}, 0)


def worker(mode, directory):
    parser = SnapshotParser(directory, block=mode == 'interrupt')
    service = build_local_parsing(Settings(_env_file=None, data_root=directory / 'data'), parser=parser)
    source = service.prepare((ROOT / 'samples/synthetic/bridge-demo-001/input/bridge-demo-001.pdf').read_bytes(),
                'source.pdf', ImportContext(DocumentKind.INSPECTION_REPORT, SourceKind.SYNTHETIC, 'B-DEMO-001')).source
    if mode == 'recover':
        operations = service.store.list_operations()
        result = {'statuses': [o['status'] for o in operations], 'parser_calls': parser.calls,
                  'manifest': service.store.read_published(source.document_id)}
    else:
        processed = service.process(source.document_id)
        result = {'reused': processed['reused'], 'parser_calls': parser.calls,
                  'manifest': service.read(source.document_id)}
    print(json.dumps(result, ensure_ascii=False))


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('--worker', choices=['complete', 'interrupt', 'recover'])
    cli.add_argument('--directory', type=Path)
    cli.add_argument('--output', type=Path)
    args = cli.parse_args()
    if args.worker:
        worker(args.worker, args.directory)
        return
    checks = {}
    def run(mode, directory):
        result = subprocess.run([sys.executable, __file__, '--worker', mode, '--directory', str(directory)],
                                text=True, capture_output=True, check=True, timeout=20)
        return json.loads(result.stdout)
    with tempfile.TemporaryDirectory(prefix='ontology-t04-') as temporary:
        directory = Path(temporary)
        complete = directory / 'complete'
        complete.mkdir()
        first = run('complete', complete)
        second = run('complete', complete)
        checks['first_process_publishes_fixture'] = first['parser_calls'] == 1 and not first['reused']
        checks['restart_reads_without_parse'] = second['parser_calls'] == 0 and second['reused']
        checks['restart_keeps_same_manifest'] = first['manifest'] == second['manifest']
        interrupted = directory / 'interrupted'
        interrupted.mkdir()
        process = subprocess.Popen([sys.executable, __file__, '--worker', 'interrupt', '--directory', str(interrupted)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 15
            while not (interrupted / 'entered.marker').exists():
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('Fixture did not reach the persisted parsing stage')
                time.sleep(0.02)
            process.terminate()
            process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        recovered = run('recover', interrupted)
        checks['terminated_process_marked_interrupted'] = recovered['statuses'] == ['interrupted']
        checks['recovery_never_calls_parser'] = recovered['parser_calls'] == 0
        checks['interruption_does_not_publish'] = recovered['manifest'] is None
    result = {'task': 'S2.1-T04', 'passed': all(checks.values()), 'checks': checks,
              'boundary': 'Actual filesystem writes, PDF page-geometry reads, subprocess restart and SIGTERM; saved-native parser is an explicit fixture. No Docling conversion/model API/Neo4j. Temporary data and owned processes cleaned.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'task': result['task'], 'passed': result['passed'], 'checks': len(checks)}))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
