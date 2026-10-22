"""Execute only the separately approved 64k proposal in a fresh run directory."""
import argparse
import hashlib
import json
from pathlib import Path
import real_shengli as base

ROOT = base.ROOT
PRIOR_DATA = base.DATA
base.OUT = ROOT / 'specs/008-system-acceptance/results/real-shengli-20261012-02'
base.DATA = ROOT / 'data/s52-real-shengli-20261012-02'
PROPOSAL = PRIOR_DATA / 'proposed-extraction-request-v2-64000.json'
PROPOSAL_SHA = 'b710488a8361e76aa51a7cd24940ffd026cee3c269bc54816ecfa0a64079e090'
Original = base.CompactExtractor


class ApprovedExtractor(Original):
    def configuration(self):
        request = json.loads(PROPOSAL.read_text())
        return {**super().configuration(), 'max_tokens': 64000,
                'prompt_sha256': hashlib.sha256(request['messages'][0]['content'].encode()).hexdigest(),
                'independent_run': 'real-shengli-20261012-02'}

    def request(self, document):
        assert base.sha(PROPOSAL) == PROPOSAL_SHA
        derived = super().request(document)
        approved = json.loads(PROPOSAL.read_text())
        assert approved['max_tokens'] == derived['max_tokens'] == 64000
        assert approved['messages'][1] == derived['messages'][1]
        assert approved['messages'][0]['content'].startswith(derived['messages'][0]['content'])
        assert {k:v for k,v in approved.items() if k != 'messages'} == {k:v for k,v in derived.items() if k != 'messages'}
        return approved


base.CompactExtractor = ApprovedExtractor


def prepare():
    assert base.sha(PROPOSAL) == PROPOSAL_SHA
    base.prepare()
    prior = [p for p in PRIOR_DATA.rglob('*') if p.is_file()]
    base.write(base.DATA / 'protected-first-run.json', {str(p.relative_to(ROOT)): base.sha(p) for p in prior})
    base.write(base.OUT / 'wrapper-freeze.json', {'files': {
        str(p.relative_to(ROOT)): base.sha(p) for p in [Path(__file__), PROPOSAL, base.OUT / 'execution.md']},
        'max_tokens': 64000, 'additional_extraction_calls': 1,
        'authorization': '允许按新预算继续', 'first_run_not_overwritten': True,
        'all_blocks_and_tables_same_as_first_run': True})


def extract():
    for name, digest in json.loads((base.OUT / 'wrapper-freeze.json').read_text())['files'].items():
        assert base.sha(ROOT / name) == digest
    base.extract()


if __name__ == '__main__':
    parser = argparse.ArgumentParser();parser.add_argument('mode', choices=['prepare','extract'])
    args = parser.parse_args()
    prepare() if args.mode == 'prepare' else extract()
