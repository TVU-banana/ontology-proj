"""Final system-run protection, secret scan, service inventory and link checks."""
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
from urllib.parse import unquote,urlsplit

from neo4j import GraphDatabase
from ontology_proj.settings import Settings
from prepare_system import ROOT, PHASE, OUT, DATA, sha, write_json
from preflight import graph_snapshot


def main():
    protected=json.loads((OUT/'protected-files.json').read_text())
    # The packaging fix is applied only after timed freeze verification. No
    # original source/experiment/result file is allowed to drift.
    allowed=['pyproject.toml']
    changed=[n for n,h in protected.items() if n not in allowed and (not (ROOT/n).is_file() or sha(ROOT/n)!=h)]
    old=Settings(_env_file=ROOT/'data/s23-runtime/.env.s32');new=Settings(_env_file=DATA/'private/.env.app')
    driver=GraphDatabase.driver(old.neo4j_uri,auth=(old.neo4j_user,old.neo4j_password.get_secret_value()))
    try:graph=graph_snapshot(driver,old.neo4j_database)
    finally:driver.close()
    expected=json.loads((OUT/'baseline.json').read_text())['old_graph']
    secrets={old.llm_api_key.get_secret_value(),old.neo4j_password.get_secret_value(),new.neo4j_password.get_secret_value()}
    secrets.discard('')
    known=['.handoff/latest.md','AGENTS.md','README.md','spec.md','pyproject.toml','docs/mvp-roadmap.md','docs/adr/README.md','docs/adr/ADR-013-S5.2系统稳定性证据边界.md','deploy/s52-compose.yaml']
    files=[ROOT/n for n in known]+[p for p in PHASE.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc']
    hits=[str(p.relative_to(ROOT)) for p in files if any(s.encode() in p.read_bytes() for s in secrets)]
    broken=[];links=0
    captured=json.loads((OUT/'frozen-harness-manifest.json').read_text())
    logical_paths={str((OUT/n).resolve()):ROOT/v['original'] for n,v in captured.items()}
    for p in files:
        if p.suffix!='.md':continue
        for value in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)',p.read_text()):
            url=value.strip().split(' "')[0].strip('<>');v=urlsplit(url)
            if v.scheme or not v.path or v.path.startswith('/'):continue
            # Captured original Markdown retains its original relative links.
            # Resolve through the immutable original-path manifest, never edit it.
            logical=logical_paths.get(str(p.resolve()),p)
            target=(logical.parent/unquote(v.path)).resolve();links+=1
            if target.name=='closure.json' and target.parent==OUT:continue
            if not target.exists():broken.append({'file':str(p.relative_to(ROOT)),'url':url});continue
            if v.fragment and target.suffix=='.md':
                anchors=[re.sub(r'[^\w\s-]','',t.replace('`','').lower()).replace(' ','-') for t in re.findall(r'^#+\s+(.+)$',target.read_text(),flags=re.M)]
                if unquote(v.fragment) not in anchors:broken.append({'file':str(p.relative_to(ROOT)),'url':url,'reason':'anchor_missing'})
    listening={}
    for port in [18000,18100,17687,18687,11434]:
        with socket.socket() as sock:listening[str(port)]=sock.connect_ex(('127.0.0.1',port))==0
    original=json.loads((OUT/'baseline.json').read_text())['copied_file_hashes']
    copied_changed=[n for n,h in original.items() if not (new.data_root/n).is_file() or sha(new.data_root/n)!=h]
    result={'protected_count':len(protected)-len(allowed),'unexpected_protected_changes':changed,'planned_changes':allowed,
        'planned_change_reason':'packaging-only exclude prevents PDF.js duplicate inclusion; applied after timed experiment exits and freeze verification',
        'old_graph':graph,'old_graph_unchanged':graph==expected,'copied_seed_files_changed':copied_changed,
        'exact_known_secret_matches':len(hits),'secret_hit_files':hits,'secret_scan_boundary':'exact current LLM key and old/new graph passwords; not complete security audit',
        'links_checked':links,'broken_links':broken,'listening_ports':listening,
        'passed':not changed and graph==expected and not copied_changed and not hits and not broken and not listening['18100']}
    write_json(OUT/'closure.json',result)
    print(json.dumps({k:result[k] for k in ['protected_count','unexpected_protected_changes','old_graph_unchanged','exact_known_secret_matches','broken_links','listening_ports','passed']},ensure_ascii=False))
    raise SystemExit(0 if result['passed'] else 1)


if __name__=='__main__':main()
