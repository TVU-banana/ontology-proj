"""Bounded real extraction of two unchanged PDFs using verified saved parses.

prepare is offline. run requires authorization for the prepared text to the relay.
No PDF conversion, graph, embeddings, service restart or source mutation.
"""
from pathlib import Path
import hashlib
import json
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'data/catalog-evidence-20261016-02'
RUNTIME = DATA / 'runtime'
PREVIOUS = ROOT / 'data/simplified-pdf-test-20261015-01/runtime/documents'
FILES = ('source.pdf','source.json','docling.json','normalized.json','manifest.json')
SAMPLES = {
    'shengli': ('6fd07b5e3fe7ff111f09a2e941b133e84b9ba8859cc2071f965b0a807b2aab6c','平湖市胜利大桥','浙江省平湖市胜利路'),
    'datong': ('f929649bbdff6acb0ba602794bd95a3e69fa597dbd15bf5047eac22148b51bed','大同前顾桥','浙江省宁波市北仑区泰山路'),
}


def save(path, value):
    with path.open('x') as f:
        json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')


def setup():
    from ontology_proj.settings import Settings
    from ontology_proj.bootstrap import build_business_extraction
    settings=Settings(_env_file=ROOT/'data/report-demo-20261014-take01/private/.env.app',
                      data_root=RUNTIME,identity_registry_path=None,identity_registry_sha256='')
    assert settings.llm_base_url.rstrip('/')=='http://relay.example.invalid:7863/v1'
    return settings,build_business_extraction(settings)


def prepare():
    DATA.mkdir()
    settings,service=setup()
    for key,(sha,name,location) in SAMPLES.items():
        did='doc_'+sha;target=RUNTIME/'documents'/did;target.mkdir(parents=True)
        for filename in FILES:
            shutil.copyfile(PREVIOUS/did/filename,target/filename)
        operation=json.loads((target/'manifest.json').read_text())['operation_id']
        shutil.copytree(PREVIOUS.parent/'operations'/operation,RUNTIME/'operations'/operation)
        assert hashlib.sha256((target/'source.pdf').read_bytes()).hexdigest()==sha
        document=service.store.read_artifact(did,'normalized')
        catalog=service.validator.catalog
        bridge=catalog.register(dict(name=name,location=location,source_kind='real',official_code=None,official_namespace=None))['bridge']
        assert catalog.resolve(document,bridge['bridge_id'])['status']=='resolved'
        save(DATA/(key+'-request-preview.json'),service.extractor.request(document))
    save(DATA/'prepared.json',{'endpoint':settings.llm_base_url+'/chat/completions','models':list(service.extractor.models),
                             'samples':SAMPLES,'extractions':len(SAMPLES),'max_http_posts':len(SAMPLES)*len(service.extractor.models),
                             'payload':'parsed text, tables, source-backed geometry, candidate schema and bridge binding; no PDF pixels or independent expected answers',
                             'parse':'hash-verified saved artifacts, no new Docling invocation'})
    print('Offline requests prepared:',DATA)


def run():
    from ontology_proj.adapters.ingestion_store import ProcessLease
    settings,service=setup()
    assert (DATA/'prepared.json').exists() and not (DATA/'started.json').exists()
    save(DATA/'started.json',{'extractions':len(SAMPLES),'models':list(service.extractor.models),'automatic_content_retries':0})
    lease=ProcessLease(service.store);lease.acquire()
    results=[]
    try:
        for key,(sha,_,_) in SAMPLES.items():
            did='doc_'+sha
            document=service.store.read_artifact(did,'normalized')
            assert service.extractor.request(document)==json.loads((DATA/(key+'-request-preview.json')).read_text())
            result=service.execute(did)
            results.append(dict(sample=key,**result))
            save(DATA/(key+'-result.json'),results[-1])
            print(json.dumps(results[-1],ensure_ascii=False),flush=True)
    finally:
        lease.release()
    save(DATA/'results.json',results)


if __name__=='__main__':
    {'prepare':prepare,'run':run}[sys.argv[1]]()
