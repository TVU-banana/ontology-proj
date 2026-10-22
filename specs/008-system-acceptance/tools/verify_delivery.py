"""Verify a real installed wheel and protected files without starting services."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[3]
PHASE = ROOT / 'specs/008-system-acceptance'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--python', required=True)
    parser.add_argument('--wheel', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    out = Path(args.output)
    if out.exists():
        raise RuntimeError('Choose a fresh output; never overwrite evidence')
    wheel = Path(args.wheel)
    mismatches = []
    names = []
    with zipfile.ZipFile(wheel) as archive:
        for p in (ROOT / 'src/ontology_proj').rglob('*'):
            if not p.is_file() or '__pycache__' in p.parts or p.suffix == '.pyc':
                continue
            name = str(p.relative_to(ROOT / 'src'))
            names.append(name)
            if name not in archive.namelist() or archive.read(name) != p.read_bytes():
                mismatches.append(name)
        forbidden = [n for n in archive.namelist() if '.env' in n or n.startswith(('data/', 'experiments/'))]
    code = '''import json
from pathlib import Path
import ontology_proj.bootstrap
import ontology_proj.interfaces.http.app
import ontology_proj.local_cli
import ontology_proj
p = Path(ontology_proj.__file__).resolve().parent
assert "site-packages" in p.parts
assert (p / "interfaces/http/static/workspace.html").is_file()
assert (p / "interfaces/http/static/query.html").is_file()
print(json.dumps({"installed_package": str(p), "bootstrap_imported": True, "static_entrypoints_present": True}))
'''
    imported = subprocess.run([args.python, '-I', '-c', code], cwd='/private/tmp', capture_output=True, text=True)
    result = dict(wheel_sha256=sha(wheel), wheel_bytes=wheel.stat().st_size,
                  source_files_compared=len(names), mismatches=mismatches, forbidden_paths=forbidden,
                  installed_import_exit_code=imported.returncode,
                  installed_import=json.loads(imported.stdout) if imported.returncode == 0 else None,
                  passed=not mismatches and not forbidden and imported.returncode == 0,
                  boundary='installed wheel imports/static bytes; no app, Docling conversion or provider invocation')
    # Do not copy exception strings containing system paths into the evidence.
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
