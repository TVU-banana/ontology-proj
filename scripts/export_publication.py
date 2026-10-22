"""Export a reviewed source snapshot without copying the private Git history.

Only tracked files are candidates. Raw evidence remains in the source checkout.
Run --check in the exported repository before every commit/push.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess


PRIVATE_SUFFIXES = {'.pem', '.key', '.p12', '.pfx', '.keystore', '.jks', '.log',
                    '.sqlite', '.sqlite3', '.db', '.dump', '.bak'}
PRIVATE_NAMES = {'.envrc', '.netrc', '.npmrc', '.pypirc', 'credentials.json',
                 'secrets.json', 'secrets.yaml', 'secrets.yml'}
PRIVATE_PARTS = {'.git', '.venv', 'venv', 'data', 'models', '.cache', '.agents',
                 '.codex', '.idea', '.vscode', '__pycache__', 'node_modules'}
USER_PATH = re.compile(r'/' + r'Users/[^/\s"\x27\\]+')
LOCAL_HOST = re.compile(r'(?i)(?<![\w.-])[a-z0-9][a-z0-9-]*\.local\b')
PRIVATE_IP = re.compile(r'(?<=://)(?:10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)(?=[:/\s"\x27]|$)')
EMAIL = re.compile(r'\b[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')
LOCAL_AGENT = re.compile(r'\bcom\.[A-Za-z0-9_-]+\.local-embedding\b')
HANDOFF = '.handoff/latest.md'
PUBLIC_HANDOFF = '''# 当前交接（公开源码快照）

此仓库从本地项目导出，不含旧 Git 历史、个人邮箱、私有配置或原始运行证据。
代码、契约与已审阅合成测试样本可用；真实资料、模型响应和原始验收记录留在本地。
历史文档中的 results、审计、演示和截图链接可能指向未公开的归档。
发布方式及验证边界见 [发布说明](../docs/github-publication.md)。
ADR 检查：无产品决策变化；公开版本的证据范围与原始项目不同。
'''


def allowed(path: str, policy: dict) -> bool:
    p = PurePosixPath(path)
    if p.is_absolute() or '..' in p.parts or not p.parts:
        return False
    name = p.name.lower()
    if any(part in PRIVATE_PARTS for part in p.parts):
        return False
    if name in PRIVATE_NAMES or p.suffix.lower() in PRIVATE_SUFFIXES:
        return False
    if name.startswith(('id_rsa', 'id_ed25519')):
        return False
    if (name.startswith('.env') or '.env.' in name or name.endswith('.env')) and name != '.env.example':
        return False
    if path == HANDOFF:
        return True  # The exporter replaces this file, never copies it.
    if path in policy['synthetic_fixtures']:
        return True
    if path.startswith(tuple(policy['private_prefixes'])) or 'results' in p.parts:
        return False
    if path.startswith('input-pdfs/'):
        return path == 'input-pdfs/README.md'
    if path.startswith('docs/ui-ux/') and p.suffix != '.md':
        return False
    if p.suffix.lower() in {'.pdf', '.png', '.jpg', '.jpeg'} and not path.startswith('samples/synthetic/'):
        return False
    if len(p.parts) == 1:
        return path in policy['allowed_root_files']
    return p.parts[0] in policy['allowed_roots']


def sanitize(path: str, data: bytes) -> bytes:
    # Preserve upstream runtime code, attribution and licenses byte-for-byte.
    if path.startswith('src/ontology_proj/interfaces/http/static/vendor/'):
        return data
    if b'\0' in data[:8192]:
        return data
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return data
    text = USER_PATH.sub('/path/to/user', text)
    text = LOCAL_HOST.sub('relay.example.invalid', text)
    text = PRIVATE_IP.sub('192.0.2.1', text)
    text = EMAIL.sub('operator@example.invalid', text)
    text = LOCAL_AGENT.sub('com.example.local-embedding', text)
    return text.encode('utf-8')


def tracked(root: Path) -> list[str]:
    return subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z']).decode().split('\0')[:-1]


def check(root: Path, policy: dict) -> list[str]:
    errors = []
    paths = tracked(root)
    for path in sorted(set(policy['required_files'] + policy['synthetic_fixtures']) - set(paths)):
        errors.append(f'required file is not tracked: {path}')
    for path in paths:
        p = root / path
        if not allowed(path, policy):
            errors.append(f'excluded path is tracked: {path}')
        elif p.is_symlink() or not p.is_file():
            errors.append(f'not a regular file: {path}')
        elif path == HANDOFF:
            if p.read_text() != PUBLIC_HANDOFF:
                errors.append('public handoff must not contain the local archive')
        elif sanitize(path, p.read_bytes()) != p.read_bytes():
            errors.append(f'private path/host/email remains: {path}')
    return errors


def export(root: Path, output: Path, policy: dict) -> dict:
    root, output = root.resolve(), output.resolve()
    if output == root or output in root.parents:
        raise ValueError('output must not replace the source repository or its parents')
    # Refuse reuse: this prevents stale files or an old .git directory entering a release.
    output.mkdir(parents=True, exist_ok=False)
    report = {'included': [], 'excluded': [], 'sanitized': []}
    for path in tracked(root):
        if not allowed(path, policy):
            report['excluded'].append(path)
            continue
        p = root / path
        if p.is_symlink() or not p.is_file():
            raise ValueError(f'not a regular file: {path}')
        raw = p.read_bytes()
        data = PUBLIC_HANDOFF.encode() if path == HANDOFF else sanitize(path, raw)
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(p.stat().st_mode & 0o777)
        report['included'].append({'path': path, 'sha256': hashlib.sha256(data).hexdigest()})
        if data != raw:
            report['sanitized'].append(path)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path.cwd())
    parser.add_argument('--output', type=Path)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    policy = json.loads((args.source / '.publication.json').read_text())
    if args.check:
        errors = check(args.source, policy)
        for error in errors:
            print(error)
        print(f'Publication policy: {len(errors)} violations')
        return int(bool(errors))
    if args.output is None:
        parser.error('--output is required for export')
    report = export(args.source, args.output, policy)
    # Keep the inventory beside the export, outside the published tree.
    args.output.with_suffix('.manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(f"Exported {len(report['included'])} files; excluded {len(report['excluded'])}; sanitized {len(report['sanitized'])}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
