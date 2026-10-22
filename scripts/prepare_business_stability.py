"""Freeze semantic 1.1 inputs/independent expectations before any model call."""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from ontology_proj.adapters.business_store import LocalBusinessStore
from ontology_proj.adapters.document_normalizer import DoclingNormalizer
from ontology_proj.adapters.fact_extractor import RelayFactExtractor, canonical
from ontology_proj.application.dto import ImportContext
from ontology_proj.application.local_parsing import LocalParsingService
from ontology_proj.application.parsing import ParseOutcome, ParseStatus
from ontology_proj.domain.source import DocumentKind, SourceKind
from ontology_proj.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    data, out = args.data_root.resolve(), args.output_dir.resolve()
    if data.exists() or out.exists(): parser.error('Use fresh paths; never overwrite frozen inputs')
    out.mkdir(parents=True)
    settings = Settings(_env_file=args.env_file, data_root=data)
    extractor = RelayFactExtractor(settings.llm_base_url, settings.llm_model, settings.llm_api_key.get_secret_value())
    expected = copy.deepcopy(json.loads((ROOT/'samples/synthetic/bridge-variants-001/expectations.json').read_text()))
    expected['expectations_version'] = 'semantic-1.1'
    expected['status'] = 'fixed_before_new_api_calls'
    expected['derived_from_semantic_rules'] = ['contract §4.4 bridgehead canonical location', '§5.1 purpose-based statements', '§6.1 nullable component or verified context chain']
    for case in expected['cases']:
        for negative in case['expected']['negative_statements']:
            if negative['component_code']=='C-DECK-01':
                negative['location_text']='桥头区域';negative['allowed_location_text']=['桥头区域']
    write(out/'expectations-1.1.json',expected)
    store = LocalBusinessStore(data)
    cases = []
    for case in expected['cases']:
        name = case['case_id']
        pdf = ROOT/case['planned_pdf']
        native_path = ROOT/'specs/002-ingestion-foundation/results/s2.1/t06-run-003/docling.json' if name=='baseline' else ROOT/'experiments/docling-variants-001/results'/name/'docling.json'
        native = json.loads(native_path.read_text())
        class SnapshotParser:
            def configuration(self): return {'fixture': 'saved-native-for-business-stability'}
            def parse(self,path,docid): return ParseOutcome(docid,ParseStatus.SUCCESS,native,(),{'fixture':'saved'},0)
        service=LocalParsingService(store,SnapshotParser(),DoclingNormalizer())
        source=service.prepare(pdf.read_bytes(),pdf.name,ImportContext(DocumentKind.INSPECTION_REPORT,SourceKind.SYNTHETIC,'B-DEMO-001')).source
        service.process(source.document_id)
        document=store.read_artifact(source.document_id,'normalized')
        write(out/(name+'-normalized.json'),document)
        write(out/(name+'-request.json'),extractor.request(document))
        cases.append({'case_id':name,'document_id':source.document_id,'pdf':case['planned_pdf'],'pdf_sha256':sha(pdf),'native_sha256':sha(native_path),'normalized':name+'-normalized.json','request':name+'-request.json'})
    write(out/'configuration.json',extractor.configuration())
    artifacts={p.name:sha(p) for p in out.iterdir() if p.is_file()}
    code={str(p.relative_to(ROOT)):sha(p) for p in (ROOT/'src/ontology_proj').rglob('*') if p.is_file() and p.suffix in ('.py','.json','.txt')}
    for name in ['scripts/business_stability_compare.py','scripts/prepare_business_stability.py','scripts/execute_business_stability.py','pyproject.toml','uv.lock']:
        code[name]=sha(ROOT/name)
    immutable_parse={str(p.relative_to(data)):sha(p) for p in data.rglob('*.json')}
    result={'configuration_version':'1.1','semantic_revision':'1.1','planned_calls':10,'calls_per_case':2,'first_pass_required':10,'cases':cases,'frozen_artifacts':artifacts,'frozen_code':code,'immutable_parse_artifacts':immutable_parse,'data_root':str(data.relative_to(ROOT)), 'boundary':'Fresh application normalized-0.1 inputs derived from saved native snapshots; not new Docling conversions. Independent expectations derived before calls, never in model requests; no AI calls in preparation.'}
    write(out/'freeze.json',result)
    print(json.dumps({'frozen':True,'cases':len(cases),'planned_calls':10,'model_calls':0}))


if __name__=='__main__': main()
