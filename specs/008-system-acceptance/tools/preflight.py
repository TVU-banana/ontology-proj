"""Read-only S5.2 inventory. No app startup, graph migration or generation."""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import subprocess
from datetime import datetime, timezone
from urllib.request import urlopen

from ontology_proj.settings import Settings
from ontology_proj.adapters.model_probe import RelayModelProbe
from neo4j import GraphDatabase

ROOT = Path(__file__).resolve().parents[3]


def digest(path):
    with path.open('rb') as stream:
        h = hashlib.file_digest(stream, 'sha256')
    return h.hexdigest()


def graph_snapshot(driver, database):
    with driver.session(database=database) as session:
        nodes = session.run('MATCH (n) RETURN labels(n) AS labels, properties(n) AS props').data()
        rels = session.run('MATCH (a)-[r]->(b) RETURN labels(a) AS al, properties(a) AS a, type(r) AS type, properties(r) AS r, labels(b) AS bl, properties(b) AS b').data()
    encode = lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False, default=str)
    raw = encode([sorted(nodes, key=encode), sorted(rels, key=encode)])
    return {'nodes': len(nodes), 'relationships': len(rels), 'sha256': hashlib.sha256(raw.encode()).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=False)
    settings = Settings(_env_file=args.config)
    # Preserve all preexisting evidence and runtime files, including failures.
    files = {}
    bases = ['experiments', 'samples', 'specs', 'docs/ui-ux', 'data']
    for base in bases:
        for p in (ROOT / base).rglob('*'):
            if not p.is_file() or p.is_symlink() or out.resolve() in p.resolve().parents:
                continue
            if p.name.startswith('.env') or '__pycache__' in p.parts or p.suffix == '.pyc':
                continue
            files[str(p.relative_to(ROOT))] = digest(p)
    for p in (ROOT / 'src').rglob('*'):
        if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc':
            files[str(p.relative_to(ROOT))] = digest(p)
    for name in ['uv.lock', 'pyproject.toml']:
        files[name] = digest(ROOT / name)
    (out / 'protected-files.json').write_text(json.dumps(files, ensure_ascii=False, indent=2))
    def git(*cmd):
        return subprocess.run(['git', *cmd], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    safe = dict(data_root=str(settings.data_root.relative_to(ROOT)), neo4j_uri=settings.neo4j_uri,
                neo4j_database=settings.neo4j_database, llm_base_url=settings.llm_base_url,
                llm_model=settings.llm_model, llm_key_present=bool(settings.llm_api_key.get_secret_value()),
                graph_password_present=bool(settings.neo4j_password.get_secret_value()),
                embedding_base_url=settings.embedding_base_url, embedding_model=settings.embedding_model,
                embedding_dimension=settings.embedding_dimension, embedding_model_digest=settings.embedding_model_digest,
                parser_artifacts_present=bool(settings.docling_artifacts_path and settings.docling_artifacts_path.is_dir()),
                app_port=settings.app_port)
    result = dict(time_utc=datetime.now(timezone.utc).isoformat(), branch=git('branch', '--show-current'),
                  head=git('rev-parse', 'HEAD'), status=git('status', '--short'), configuration=safe,
                  protected_file_count=len(files), services={}, generation_calls=0, embedding_calls=0,
                  graph_writes=0, boundary='read-only probes; models/tags are not generation proof')
    for name, port in [('previous_http', 18000), ('neo4j_tcp', 17687), ('ollama_tcp', 11434)]:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=2):
                result['services'][name] = {'listening': True}
        except OSError as exc:
            result['services'][name] = {'listening': False, 'error_type': type(exc).__name__}
    driver = None
    try:
        driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()), connection_timeout=3)
        driver.verify_connectivity()
        result['services']['neo4j'] = {'connected': True, 'snapshot': graph_snapshot(driver, settings.neo4j_database)}
    except Exception as exc:
        result['services']['neo4j'] = {'connected': False, 'error_type': type(exc).__name__}
    finally:
        if driver:
            driver.close()
    try:
        with urlopen(settings.embedding_base_url.rstrip('/') + '/api/tags', timeout=4) as r:
            tags = json.load(r)
        match = [v for v in tags.get('models', []) if v.get('name') == settings.embedding_model]
        result['services']['ollama'] = {'reachable': True, 'configured_model_present': bool(match),
            'digest_matches': bool(match and match[0].get('digest') == settings.embedding_model_digest)}
    except Exception as exc:
        result['services']['ollama'] = {'reachable': False, 'error_type': type(exc).__name__}
    probe = RelayModelProbe(settings.llm_base_url, settings.llm_model, settings.llm_api_key.get_secret_value()).check()
    result['services']['relay'] = {'state': probe.state.value, 'error_code': probe.error_code}
    (out / 'preflight.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'output': str(out), 'protected_file_count': len(files), 'services': result['services']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
