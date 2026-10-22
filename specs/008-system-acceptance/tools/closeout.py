"""Read-only graph/file comparison and exact-secret scan of deliverable changes."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit

from neo4j import GraphDatabase
from ontology_proj.settings import Settings
from preflight import graph_snapshot

ROOT = Path(__file__).resolve().parents[3]
PHASE = ROOT / 'specs/008-system-acceptance'


def sha(p):
    with p.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--baseline-dir', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    out = Path(args.output).resolve()
    if out.exists():
        raise RuntimeError('Choose a fresh output')
    baseline = Path(args.baseline_dir)
    files = json.loads((baseline / 'protected-files.json').read_text())
    old = json.loads((baseline / 'preflight.json').read_text())
    excluded = [n for n in files if n.startswith('specs/008-system-acceptance/') or n == 'pyproject.toml']
    protected = {n: h for n, h in files.items() if n not in excluded}
    changed = [n for n, h in protected.items() if not (ROOT / n).is_file() or sha(ROOT / n) != h]
    settings = Settings(_env_file=args.config)
    driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()))
    try:
        graph = graph_snapshot(driver, settings.neo4j_database)
    finally:
        driver.close()
    secrets = [v for v in (settings.llm_api_key.get_secret_value(), settings.neo4j_password.get_secret_value()) if v]
    root_changes = ['.handoff/latest.md', 'AGENTS.md', 'README.md', 'docs/mvp-roadmap.md', 'spec.md', 'pyproject.toml']
    deliverables = [ROOT / n for n in root_changes]
    deliverables += [p for p in PHASE.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc']
    secret_hits = []
    for p in deliverables:
        raw = p.read_bytes()
        if any(s.encode() in raw for s in secrets):
            secret_hits.append(str(p.relative_to(ROOT)))
    broken = []
    checked = 0
    for p in deliverables:
        if p.suffix != '.md':
            continue
        for raw in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', p.read_text()):
            url = raw.strip().split(' "')[0].strip('<>')
            split = urlsplit(url)
            if split.scheme or not split.path or split.path.startswith('/'):
                continue
            target = (p.parent / unquote(split.path)).resolve()
            checked += 1
            if target != out and not target.exists():
                broken.append({'file': str(p.relative_to(ROOT)), 'target': url})
                continue
            if split.fragment and target.exists() and target.suffix == '.md':
                anchors = []
                for title in re.findall(r'^#+\s+(.+)$', target.read_text(), flags=re.M):
                    title = re.sub(r'[^\w\s-]', '', title.replace('`', '').lower()).replace(' ', '-')
                    anchors.append(title)
                if unquote(split.fragment) not in anchors:
                    broken.append({'file': str(p.relative_to(ROOT)), 'target': url, 'reason': 'anchor_missing'})
    diff = subprocess.run(['git', 'diff', '--check', '--', *root_changes], cwd=ROOT, capture_output=True, text=True)
    result = {'protected_count': len(protected), 'unchanged_count': len(protected) - len(changed), 'unexpected_changes': changed,
        'planned_exclusions': excluded, 'exclusion_reason': 'new mutable phase drafts and explicit PDF.js packaging fix; original hashes retained',
        'old_graph': graph, 'old_graph_unchanged': graph == old['services']['neo4j']['snapshot'],
        'local_links_checked': checked, 'broken_links': broken,
        'exact_configured_secret_matches': len(secret_hits), 'secret_hit_files': secret_hits,
        'secret_scan_boundary': 'exact configured LLM key/graph password only; not comprehensive security audit',
        'diff_check_exit_code': diff.returncode, 'diff_check_output': diff.stdout,
        'old_ui_preserved': all(n not in changed for n in protected if n.startswith('src/ontology_proj/interfaces/http/static/')),
        'generation_calls': 0, 'embedding_calls': 0, 'graph_writes': 0,
        'passed': not changed and graph == old['services']['neo4j']['snapshot'] and not broken and not secret_hits and diff.returncode == 0}
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: result[k] for k in ['protected_count', 'unchanged_count', 'old_graph_unchanged', 'broken_links', 'exact_configured_secret_matches', 'passed']}, ensure_ascii=False))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
