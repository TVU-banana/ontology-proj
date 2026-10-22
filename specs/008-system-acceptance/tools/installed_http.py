"""Installed-wheel TCP/static smoke, deliberately no graph or model config."""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[3]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--python', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=False)
    checks = []
    with tempfile.TemporaryDirectory(prefix='ontology-s52-installed-', dir='/private/tmp') as runtime:
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
        config = Path(runtime) / '.env'
        config.write_text(f'APP_PORT={port}\nDATA_ROOT={runtime}/data\n')
        with (out / 'server.log').open('w') as log:
            process = subprocess.Popen([args.python, '-I', '-m', 'ontology_proj', '--env-file', str(config)],
                cwd=runtime, env={'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1'}, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError('Installed app exited before health')
                    try:
                        with urlopen(f'http://127.0.0.1:{port}/health', timeout=1) as r:
                            if r.status == 200:
                                break
                    except (URLError, OSError):
                        time.sleep(.1)
                else:
                    raise RuntimeError('Installed app did not reach health')
                paths = ['/', '/query', '/static/vendor/pdfjs/build/pdf.mjs', '/static/vendor/pdfjs/build/pdf.worker.mjs']
                for path in paths:
                    with urlopen(f'http://127.0.0.1:{port}' + path, timeout=3) as r:
                        raw = r.read()
                        expected = (ROOT / 'src/ontology_proj/interfaces/http' / path.lstrip('/')).read_bytes() if path.startswith('/static/') else None
                        checks.append({'path': path, 'http_status': r.status,
                            'passed': r.status == 200 and (expected is None or raw == expected),
                            'sha256': hashlib.sha256(raw).hexdigest()})
                try:
                    with urlopen(f'http://127.0.0.1:{port}/ready', timeout=3) as r:
                        checks.append({'path': '/ready', 'passed': False, 'http_status': r.status})
                except HTTPError as exc:
                    result = json.loads(exc.read())
                    checks.append({'path': '/ready', 'http_status': exc.code,
                        'passed': exc.code == 503, 'body': result})
            except Exception as exc:
                checks.append({'name': 'smoke_exception', 'passed': False, 'error_type': type(exc).__name__})
            finally:
                process.terminate()
                process.wait(timeout=15)
                checks.append({'name': 'owned_process_stopped', 'passed': process.poll() is not None})
    result = {'checks': checks, 'passed': all(c['passed'] for c in checks),
        'boundary': 'real installed-wheel HTTP without external configuration; not graph/model/system stability',
        'generation_calls': 0, 'embedding_calls': 0, 'graph_writes': 0, 'old_data_root_used': False}
    (out / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
