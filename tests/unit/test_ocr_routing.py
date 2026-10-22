"""Policy/storage tests use doubles; actual engine evidence is in Spec013 results."""
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from ontology_proj.adapters import ocr_routing
from ontology_proj.adapters.docling_parser import DoclingParser, EXPECTED_VERSIONS, REQUIRED_MODEL_FILES
from ontology_proj.settings import Settings
from ontology_proj.bootstrap import build_parser
from ontology_proj.application.parsing import ParseStatus, ParseOutcome

ROOT = Path(__file__).resolve().parents[2]

@pytest.fixture
def assets(tmp_path, monkeypatch):
    monkeypatch.setattr(DoclingParser, 'installed_versions', lambda self: dict(EXPECTED_VERSIONS))
    monkeypatch.setattr(ocr_routing, 'version', lambda n: ocr_routing.OCR_VERSIONS[n])
    for filename in REQUIRED_MODEL_FILES:
        p=tmp_path/'docling'/filename;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'dummy')
    monkeypatch.setattr(ocr_routing, 'OCR_MODELS', {'det':('model.onnx',hashlib.sha256(b'fixture').hexdigest())})
    models=tmp_path/'ocr';models.mkdir();(models/'model.onnx').write_bytes(b'fixture')
    return tmp_path/'docling',models

@pytest.mark.parametrize('problem,code', [('unconfigured','OCR_NOT_CONFIGURED'),('missing','OCR_MODEL_FILES_UNAVAILABLE'),('corrupt','OCR_MODEL_HASH_MISMATCH'),('version','OCR_VERSION_MISMATCH'),('dependency','OCR_DEPENDENCY_UNAVAILABLE')])
def test_ocr_preflight_never_initializes_or_downloads(assets,monkeypatch,problem,code):
    base,models=assets
    monkeypatch.setattr(ocr_routing,'make_converter',lambda *a:pytest.fail('must not initialize'))
    if problem=='unconfigured':models=None
    elif problem=='missing':(models/'model.onnx').unlink()
    elif problem=='corrupt':(models/'model.onnx').write_bytes(b'changed')
    elif problem=='version':monkeypatch.setattr(ocr_routing,'version',lambda n:'unexpected')
    else:
        from importlib.metadata import PackageNotFoundError
        def absent(n):raise PackageNotFoundError(n)
        monkeypatch.setattr(ocr_routing,'version',absent)
    p=DoclingParser(base,ocr_models_path=models)
    assert p.check().error_code==code and p.initialize().code==code

@pytest.mark.parametrize('pages', [[0],[-1],[True],[1,1],['2']])
def test_invalid_force_pages_rejected(pages):
    with pytest.raises(ValueError):DoclingParser(None,ocr_force_pages=pages)
    with pytest.raises(ValidationError):Settings(docling_ocr_force_pages=pages)


def test_mode_conflict_and_environment(monkeypatch,tmp_path):
    with pytest.raises(ValueError):DoclingParser(None,ocr_mode='off',ocr_force_pages=[1])
    with pytest.raises(ValidationError):Settings(docling_ocr_mode='off',docling_ocr_force_pages=[1])
    monkeypatch.setenv('DOCLING_OCR_FORCE_PAGES','[3,1]')
    monkeypatch.setenv('DOCLING_OCR_MODELS_PATH',str(tmp_path))
    p=build_parser(Settings())
    assert p.configuration()['ocr']['force_pages']==[1,3]
    assert p._ocr_models==tmp_path.resolve()
    assert p.configuration()['do_ocr']


def test_forced_page_outside_pdf_rejected_before_initializing(assets,monkeypatch):
    base,models=assets
    monkeypatch.setattr(ocr_routing,'make_converter',lambda *a:pytest.fail('must not initialize'))
    source=ROOT/'samples/synthetic/bridge-demo-001/input/bridge-demo-001.pdf'
    result=DoclingParser(base,ocr_models_path=models,ocr_force_pages=[3]).parse(str(source),'doc_'+hashlib.sha256(source.read_bytes()).hexdigest())
    assert result.diagnostics[0].code=='OCR_FORCE_PAGE_OUT_OF_RANGE'


@pytest.mark.parametrize('page_status,expected,code',[('recognized',ParseStatus.SUCCESS,None),('skipped',ParseStatus.SUCCESS,None),('empty',ParseStatus.PARTIAL,'OCR_EMPTY_RESULT'),('failed',ParseStatus.PARTIAL,'OCR_ROUTING_FAILED')])
def test_actual_trace_attached_and_incomplete_ocr_not_published(assets,monkeypatch,tmp_path,page_status,expected,code):
    base,models=assets;path=tmp_path/'source.pdf';path.write_bytes(b'fake pdf')
    trace=[];native={'pages':{'1':{}},'texts':[]}
    def convert(*a,**k):
        trace.append({'page_no':1,'status':page_status,'reader_calls':0 if page_status=='skipped' else 1})
        return SimpleNamespace(status=SimpleNamespace(value='success'),errors=[],document=SimpleNamespace(export_to_dict=lambda:native))
    monkeypatch.setattr(ocr_routing,'make_converter',lambda *a:SimpleNamespace(convert=convert,_ontology_ocr_trace=trace))
    p=DoclingParser(base,ocr_models_path=models)
    result=p.parse(str(path),'doc_'+hashlib.sha256(path.read_bytes()).hexdigest())
    assert result.status==expected and result.ocr['pages'][0]['status']==page_status
    assert result.native_document==native and 'ocr' not in native
    assert [d.code for d in result.diagnostics]==([code] if code else [])
    p.parse(str(path),'doc_'+hashlib.sha256(path.read_bytes()).hexdigest())
    assert len(trace)==1 # previous conversion receipt is reset, never accumulated


def test_saved_trace_reaches_operation_and_normalized(tmp_path):
    from ontology_proj.adapters.local_processing_store import LocalProcessingStore
    from ontology_proj.adapters.document_normalizer import DoclingNormalizer
    from ontology_proj.application.local_parsing import LocalParsingService
    from ontology_proj.application.dto import ImportContext
    from ontology_proj.domain.source import DocumentKind,SourceKind
    import json
    native=json.loads((ROOT/'experiments/docling-parse-001/results/docling.json').read_text())
    receipt={'revision':'ocr-routing-1','mode':'auto','pages':[{'page_no':1,'reader_calls':0}]}
    class Parser:
        def configuration(self):return {'ocr':'fixture'}
        def parse(self,path,identity):return ParseOutcome(identity,ParseStatus.SUCCESS,native,(),{},0,ocr=receipt)
    store=LocalProcessingStore(tmp_path);svc=LocalParsingService(store,Parser(),DoclingNormalizer())
    source=svc.prepare((ROOT/'samples/synthetic/bridge-demo-001/input/bridge-demo-001.pdf').read_bytes(),'demo.pdf',ImportContext(DocumentKind.INSPECTION_REPORT,SourceKind.SYNTHETIC,None)).source
    result=svc.process(source.document_id)
    assert result['operation']['status'] in ('succeeded','succeeded_with_warnings')
    assert store.read_operation(result['operation_id'])['ocr']==receipt
    assert store.read_artifact(source.document_id,'normalized')['ocr']==receipt
    assert 'ocr' not in store.read_artifact(source.document_id,'docling')
