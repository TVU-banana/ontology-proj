"""Check protected bytes, local pointers and configured-key exact matches."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
from ontology_proj.settings import Settings

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'specs/007-agent-web/results/baseline-integrity.json'
OUTPUT=ROOT/'specs/007-agent-web/results/integrity.json'


def main():
    global OUTPUT
    parser=argparse.ArgumentParser();parser.add_argument('--output');args=parser.parse_args()
    if args.output:OUTPUT=ROOT/args.output
    if OUTPUT.exists():raise RuntimeError('Preserve previous result; choose new output in script')
    baseline=json.loads(BASE.read_text())
    def changed(mapping):return [p for p,h in mapping.items() if not Path(p).is_file() or hashlib.sha256(Path(p).read_bytes()).hexdigest()!=h]
    tracked_changed=changed(baseline['protected']);runtime_changed=changed(baseline['runtime'])
    files=['AGENTS.md','spec.md','README.md','docs/mvp-roadmap.md','docs/adr/README.md','.handoff/latest.md']
    files+= [str(p.relative_to(ROOT)) for p in (ROOT/'specs/007-agent-web').glob('*.md')]
    files+= ['docs/adr/ADR-012-有界角色工具与同源工作台.md','specs/007-agent-web/results/review.md']
    broken=[];count=0
    for item in files:
        p=ROOT/item
        if not p.exists():broken.append(item);continue
        for link in re.findall(r'\]\(([^)]+)\)',p.read_text()):
            if ':' in link or link.startswith('#'):continue
            target=link.split('#')[0];count+=1
            if target and not (p.parent/target).exists():broken.append([item,link])
    # New deliverables only. No key value is emitted.
    settings=Settings(_env_file=ROOT/'data/s23-runtime/.env.s32')
    secret=settings.llm_api_key.get_secret_value().encode()
    candidates=subprocess.check_output(['git','ls-files','--others','--exclude-standard','-z'],cwd=ROOT).decode().split('\0')
    candidates+=subprocess.check_output(['git','diff','--name-only','-z'],cwd=ROOT).decode().split('\0')
    leaks=[p for p in set(candidates) if p and (ROOT/p).is_file() and secret and secret in (ROOT/p).read_bytes()]
    result={'protected_tracked_count':len(baseline['protected']),'protected_tracked_changed':tracked_changed,'existing_runtime_count':len(baseline['runtime']),'existing_runtime_changed':runtime_changed,'local_pointer_count':count,'broken_pointers':broken,'configured_key_exact_match_files':leaks,'not_full_security_audit':True}
    OUTPUT.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
