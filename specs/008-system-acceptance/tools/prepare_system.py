"""Fresh S5.2 baseline/private config; read-only seed export and isolated import."""
import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import time

from neo4j import GraphDatabase
from ontology_proj.settings import Settings
from ontology_proj.adapters.neo4j_repository import Neo4jRepository
from ontology_proj.adapters.semantic_repository import Neo4jSemantic
from ontology_proj.bootstrap import build_semantic
from preflight import graph_snapshot

ROOT = Path(__file__).resolve().parents[3]
PHASE = ROOT / 'specs/008-system-acceptance'
RUN = 'system-20261012-01'
OUT = PHASE / 'results' / RUN
DATA = ROOT / 'data' / ('s52-' + RUN)
CONTAINER = 'ontology-s52-20261012-01'


def sha(p):
    with p.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def write_json(path, value):
    with path.open('x') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)


def initialize(config):
    OUT.mkdir(parents=True, exist_ok=False)
    DATA.mkdir(parents=True, exist_ok=False)
    private = DATA / 'private'
    private.mkdir(mode=0o700)
    s = Settings(_env_file=config)
    assert s.data_root == ROOT / 'data/s23-runtime'
    assert s.neo4j_uri == 'bolt://127.0.0.1:17687'
    assert s.llm_base_url == 'http://relay.example.invalid:7863/v1' and s.llm_model == 'cn:deepseek-v4.1-flash'
    for port in (18100, 18474, 18687, 9):
        with socket.socket() as sock:
            assert sock.connect_ex(('127.0.0.1', port)) != 0, 'Selected isolation/fault port is in use'
    protected = {}
    for base in ['experiments', 'samples', 'specs', 'data', 'docs/ui-ux', 'src', 'tests']:
        for p in (ROOT / base).rglob('*'):
            if not p.is_file() or p.is_symlink() or p.name.startswith('.env') or '__pycache__' in p.parts or p.suffix == '.pyc':
                continue
            if DATA in p.parents or OUT in p.parents:
                continue
            if PHASE in p.parents and 'results' not in p.relative_to(PHASE).parts:
                continue
            protected[str(p.relative_to(ROOT))] = sha(p)
    for name in ['uv.lock', 'pyproject.toml']:
        protected[name] = sha(ROOT / name)
    write_json(OUT / 'protected-files.json', protected)
    driver = GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password.get_secret_value()))
    try:
        before = graph_snapshot(driver, s.neo4j_database)
        with driver.session(database=s.neo4j_database) as session:
            nodes = session.run('MATCH (n) RETURN elementId(n) AS id,labels(n) AS labels,properties(n) AS props').data()
            rels = session.run('MATCH (a)-[r]->(b) RETURN elementId(a) AS a,elementId(b) AS b,type(r) AS type,properties(r) AS props').data()
        assert all('_s52_seed_id' not in n['props'] for n in nodes)
        write_json(private / 'seed.json', {'nodes': nodes, 'relationships': rels})
    finally:
        driver.close()
    copied = {}
    for p in s.data_root.rglob('*'):
        if not p.is_file() or p.is_symlink() or p.name.startswith('.env') or p.suffix == '.lock' or p.name.startswith('.process'):
            continue
        if 'locks' in p.relative_to(s.data_root).parts:
            continue
        dest = DATA / 'runtime' / p.relative_to(s.data_root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest)
        copied[str(dest.relative_to(DATA / 'runtime'))] = sha(dest)
    password = secrets.token_urlsafe(24)
    graph_env = private / '.env.neo4j'
    graph_env.write_text('NEO4J_AUTH=neo4j/' + password + '\n')
    graph_env.chmod(0o600)
    new = s.model_dump()
    new.update(data_root=DATA/'runtime', app_port=18100, actor_id='s52_system_20261012_01',
               neo4j_uri='bolt://127.0.0.1:18687', neo4j_user='neo4j', neo4j_password=password)
    lines = []
    for key, value in new.items():
        if hasattr(value, 'get_secret_value'):
            value = value.get_secret_value()
        if value is None:
            value = ''
        lines.append(key.upper() + '=' + json.dumps(str(value), ensure_ascii=False))
    app_env = private / '.env.app'
    app_env.write_text('\n'.join(lines) + '\n')
    app_env.chmod(0o600)
    requests = [
        {'id':'components','body':{'question':'本资料登记了哪些构件？请列出构件名称。','graph_query':{'query_type':'bridge_components'}}},
        {'id':'area','body':{'question':'观测 O-001 的规范面积和单位是什么？','selector':'O-001'}},
        {'id':'unknown-date','body':{'question':'观测 O-001 实际发生于哪一天？请仅依据资料的观测日期。','selector':'O-001'}},
        {'id':'negative','body':{'question':'桥头铺装是否有明显病害？请仅引用该处的原句。'}},
        {'id':'professional','body':{'question':'这些资料能否支持专业安全评级和处治方案？'}},
    ]
    oracle = {'components':{'status':'answered','names':['主梁','桥面铺装']},
        'area':{'status':'answered','field':'area_value','value':'0.04','unit':'m²'},
        'unknown-date':{'status':'insufficient_evidence','reason':'MISSING_DATE'},
        'negative':{'status':'answered','source_contains':'桥头铺装平顺，未见明显病害。'},
        'professional':{'status':'insufficient_evidence','reason':'PROFESSIONAL_NOT_SUPPORTED'}}
    write_json(OUT / 'model-tasks.json', requests)
    write_json(OUT / 'model-expectations.json', oracle)
    baseline = {'run_id': RUN, 'head': subprocess.run(['git','rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip(),
        'source_configuration': {'llm_base_url':s.llm_base_url,'llm_model':s.llm_model,'embedding_base_url':s.embedding_base_url,'embedding_model':s.embedding_model,'embedding_digest':s.embedding_model_digest},
        'old_graph': before, 'protected_file_count': len(protected), 'copied_file_hashes': copied,
        'seed_boundary':'read-only copy of existing synthetic publication/graph; no parsing/extraction/indexing',
        'duration_seconds':3600,'interval_seconds':30,'minimum_samples':120,'normal_report_count':10,
        'app_restarts':3,'database_restarts':1,'normal_failure_maximum':0,'model_tasks':9,'automatic_provider_retries':0,
        'app_recovery_seconds':30,'database_recovery_seconds':120,'report_timeout_seconds':30}
    write_json(OUT / 'baseline.json', baseline)
    frozen = {str(p.relative_to(ROOT)):sha(p) for p in (ROOT/'src').rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
    for p in list((PHASE/'tools').glob('*.py')) + [PHASE/'execution.md', PHASE/'expectations.md', OUT/'baseline.json', OUT/'model-tasks.json', OUT/'model-expectations.json', ROOT/'uv.lock', ROOT/'pyproject.toml']:
        frozen[str(p.relative_to(ROOT))] = sha(p)
    write_json(OUT / 'freeze.json', frozen)
    print(json.dumps({'output':str(OUT),'data_root':str(DATA/'runtime'),'protected':len(protected),'copied':len(copied),'old_graph':before},ensure_ascii=False))


def import_seed():
    s = Settings(_env_file=DATA/'private/.env.app')
    assert s.neo4j_uri == 'bolt://127.0.0.1:18687' and s.data_root == DATA/'runtime'
    graph = Neo4jRepository(s.neo4j_uri,s.neo4j_user,s.neo4j_password.get_secret_value(),s.neo4j_database)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            graph.driver.verify_connectivity()
            break
        except Exception:
            time.sleep(1)
    else:
        raise RuntimeError('Dedicated graph unavailable before import')
    seed = json.loads((DATA/'private/seed.json').read_text())
    with graph._session() as session:
        assert session.run('MATCH (n) RETURN count(n) AS n').single()['n'] == 0
        grouped = defaultdict(list)
        for n in seed['nodes']:
            assert all(re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', l) for l in n['labels'])
            grouped[tuple(sorted(n['labels']))].append(n)
        for labels, rows in grouped.items():
            label = ''.join(':' + l for l in labels)
            session.execute_write(lambda tx, label=label, rows=rows:tx.run('UNWIND $rows AS row CREATE (n'+label+') SET n += row.props, n._s52_seed_id=row.id',rows=rows).consume())
        grouped = defaultdict(list)
        for r in seed['relationships']:
            assert re.fullmatch(r'[A-Z][A-Z0-9_]*',r['type'])
            grouped[r['type']].append(r)
        for kind, rows in grouped.items():
            session.execute_write(lambda tx, kind=kind, rows=rows:tx.run('UNWIND $rows AS row MATCH (a {_s52_seed_id:row.a}), (b {_s52_seed_id:row.b}) CREATE (a)-[r:'+kind+']->(b) SET r += row.props', rows=rows).consume())
        session.run('MATCH (n) REMOVE n._s52_seed_id').consume()
    expected = json.loads((OUT/'baseline.json').read_text())['old_graph']
    copied = graph_snapshot(graph.driver,s.neo4j_database)
    assert copied == expected, 'Seed copy differs from source graph'
    graph.initialize()
    _, search, _, _ = build_semantic(s, graph, None)
    Neo4jSemantic(graph,s.embedding_dimension,s.actor_id).initialize(search.profile)
    result = {'copied_graph':copied,'source_graph_match':copied==expected,'graph_ready':graph.check().state.value,'seeded_vectors_reused':True,'generation_calls':0,'embedding_calls':0,'new_docling_calls':0}
    write_json(OUT/'seed-result.json',result)
    graph.close()
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['initialize','import']);p.add_argument('--config',default='data/s23-runtime/.env.s32');a=p.parse_args()
    initialize(a.config) if a.mode=='initialize' else import_seed()
