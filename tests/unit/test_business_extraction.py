import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ontology_proj.adapters.business_validation import BusinessValidator
from ontology_proj.adapters.business_store import LocalBusinessStore
from ontology_proj.adapters.document_normalizer import DoclingNormalizer
from ontology_proj.adapters.fact_extractor import RelayFactExtractor, strict_json
from ontology_proj.application.business_extraction import ExtractBusiness, ReadBusiness
from ontology_proj.application.dto import ImportContext
from ontology_proj.application.files import FileStoreError
from ontology_proj.application.local_parsing import LocalParsingService
from ontology_proj.application.parsing import ParseOutcome, ParseStatus
from ontology_proj.domain.source import DocumentKind, SourceKind

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def fixture(tmp_path):
    pdf = (ROOT/'samples/synthetic/bridge-demo-001/input/bridge-demo-001.pdf').read_bytes()
    native = json.loads((ROOT/'specs/002-ingestion-foundation/results/s2.1/t06-run-003/docling.json').read_text())
    class Parser:
        def configuration(self): return {'fixture': 'saved-native'}
        def parse(self, path, docid): return ParseOutcome(docid, ParseStatus.SUCCESS, native, (), {'fixture':'saved'}, 0)
    store = LocalBusinessStore(tmp_path)
    parsing = LocalParsingService(store, Parser(), DoclingNormalizer())
    docid = parsing.prepare(pdf, 'source.pdf', ImportContext(DocumentKind.INSPECTION_REPORT, SourceKind.SYNTHETIC, 'B-DEMO-001')).source.document_id
    parsing.process(docid)
    document = store.read_artifact(docid, 'normalized')
    old = json.loads((ROOT/'experiments/business-extraction-001/results/fresh/input-document.json').read_text())
    candidate = json.loads((ROOT/'experiments/business-extraction-001/results/fresh/candidate.json').read_text())
    new_ids = {b['source_ref']:b['block_id'] for b in document['blocks']}
    mapping = {b['block_id']:new_ids[b['source_ref']] for b in old['blocks']}
    def remap(value):
        if isinstance(value, list): return [remap(v) for v in value]
        if isinstance(value, dict): return {k: mapping[v] if k == 'block_id' else remap(v) for k,v in value.items()}
        return value
    candidate = remap(candidate)
    # A derived, manually completed test fixture for semantic 1.1. The saved
    # old model answer stays unchanged and is not counted as a new model pass.
    for value in candidate['statements']:
        description = next(s for s in value['sources'] if s['target_field'] == 'description')
        value['sources'].append({**description, 'target_field': 'kind'})
        if value['component_ref'] is not None:
            value['sources'].append({**description, 'target_field': 'component_ref'})
    return store, document, candidate, BusinessValidator()


def source(block, field, quote=None):
    return {'block_id':block['block_id'], 'exact_quote':quote or block['text'], 'target_field':field, 'occurrence_hint':None}


def statement(document, text_marker, *, component=None, title=False):
    block = next(b for b in document['blocks'] if text_marker in b['text'] and b['kind'] != 'table_cell')
    value = {'kind':'unmapped', 'text':block['text'], 'component_ref':component, 'location_text':None, 'sources':[source(block,'description'),source(block,'kind')]}
    if component:
        value['sources'].append(source(block,'component_ref'))
        if title:
            caption = next(b for b in document['blocks'] if b['text'].startswith('表 4'))
            value['sources'].append(source(caption,'component_ref'))
    return value


def test_schema_packaging_matches_primary_contract():
    assert (ROOT/'src/ontology_proj/resources/business.schema.json').read_bytes() == (ROOT/'specs/001-business-extraction/business.schema.json').read_bytes()


def test_baseline_and_bridgehead_normalization(fixture):
    _, doc, candidate, validator = fixture
    result = validator.validate(candidate,doc)
    assert result['records'] is not None, result['diagnostics']
    negative = next(s for s in result['records']['statements'] if s['kind']=='negative')
    assert negative['location_text'] == '桥头区域'
    assert negative['text'] == next(s for s in candidate['statements'] if s['kind']=='negative')['text']
    assert not validator.validate_records(result['records'],doc)


@pytest.mark.parametrize('wrong_kind', ['negative','aggregate','suspected'])
def test_explanatory_text_cannot_be_detection(fixture, wrong_kind):
    _, doc, candidate, validator = fixture
    value = statement(doc,'正文与表格描述')
    value['kind'] = wrong_kind
    candidate['statements'].append(value)
    result = validator.validate(candidate,doc)
    assert result['records'] is None
    assert 'STATEMENT_KIND_UNSUPPORTED' in {d['code'] for d in result['diagnostics']}


def test_retaining_distinct_identifiers_is_not_disease_total(fixture):
    _,doc,candidate,validator=fixture
    value=statement(doc,'两个露筋位置不同')
    value['text']='两个露筋位置不同，分别保留各自病害编号与观测编号。'
    candidate['statements'].append(value)
    assert validator.validate(candidate,doc)['records'] is not None
    value['kind']='aggregate'
    result=validator.validate(candidate,doc)
    assert result['records'] is None
    assert 'STATEMENT_KIND_UNSUPPORTED' in {d['code'] for d in result['diagnostics']}


def test_nullable_statement_component_and_context_chain(fixture):
    _, doc, candidate, validator = fixture
    girder = next(e['local_id'] for e in candidate['entities'] if e['entity_type']=='Component' and e['data']['component_code']=='C-GIRDER-01')
    candidate['statements'].append(statement(doc,'表中两条观测'))
    assert validator.validate(candidate,doc)['records'] is not None
    candidate['statements'][-1] = statement(doc,'表中两条观测',component=girder,title=True)
    assert validator.validate(candidate,doc)['records'] is not None
    candidate['statements'][-1]['sources'] = [s for s in candidate['statements'][-1]['sources'] if not s['exact_quote'].startswith('表 4')]
    assert validator.validate(candidate,doc)['records'] is None


@pytest.mark.parametrize('mutation',['wrong_component','nonadjacent','missing_statement_link','cross_page'])
def test_invalid_table_component_chain_rejected(fixture, mutation):
    _, doc, candidate, validator = fixture
    girder = next(e['local_id'] for e in candidate['entities'] if e['entity_type']=='Component' and e['data']['component_code']=='C-GIRDER-01')
    value = statement(doc,'表中两条观测',component=girder,title=True)
    if mutation=='wrong_component': value['component_ref'] = next(e['local_id'] for e in candidate['entities'] if e['entity_type']=='Component' and e['data']['component_code']=='C-DECK-01')
    elif mutation=='missing_statement_link': value['sources'] = [s for s in value['sources'] if s['target_field']!='component_ref' or s['exact_quote'].startswith('表 4')]
    elif mutation=='nonadjacent':
        i=next(i for i,b in enumerate(doc['blocks']) if b['block_id']==value['sources'][0]['block_id']);extra=copy.deepcopy(doc['blocks'][0]);extra['block_id']='extra';doc['blocks'].insert(i,extra)
    else:
        for b in doc['blocks']:
            if b['block_id']==value['sources'][0]['block_id']: b['locations'][0]['page_no']=1
    candidate['statements'].append(value)
    assert validator.validate(candidate,doc)['records'] is None


def test_missing_area_pending_and_unknown_date(fixture):
    _,doc,candidate,validator=fixture
    observation=next(e for e in candidate['entities'] if e['entity_type']=='Observation')
    observation['data']['area_original_value']=None
    observation['data']['area_is_approximate']=None
    result=validator.validate(candidate,doc)
    assert result['records'] is not None
    target=next(o for o in result['records']['observations'] if o['observation_code']==observation['data']['observation_code'])
    assert target['acceptance_status']=='pending_review' and target['area_value'] is None and target['observed_at'] is None


def test_use_case_publication_reuse_failure_and_hashes(fixture):
    store,doc,candidate,validator=fixture
    class Extractor:
        calls=0
        def configuration(self): return {'fixture':'candidate-1.1'}
        def request(self,doc): return {'fixture':'no model'}
        def extract(self,request): self.calls+=1;return {'response':{'fixture':True}, 'candidate':copy.deepcopy(candidate), 'diagnostics':[]}
    extractor=Extractor();service=ExtractBusiness(store,extractor,validator)
    first=service.execute(doc['document_id'])
    assert first['status']=='succeeded'
    assert service.execute(doc['document_id'])['reused'] and extractor.calls==1
    assert ReadBusiness(store).read(doc['document_id'])['run_id']==first['run_id']
    candidate['entities'][0]['data']['bridge_code']='OTHER'
    failed=service.execute(doc['document_id'],fresh=True)
    assert failed['status']=='rejected'
    assert store.read_business(doc['document_id'])['run_id']==first['run_id']
    path=store._run(doc['document_id'],first['run_id'])/'records.json';path.write_text('{}')
    with pytest.raises(FileStoreError) as error:
        ReadBusiness(store).read(doc['document_id'])
    assert error.value.code == 'ARTIFACT_MISMATCH'


def test_requests_exclude_truth_and_credentials(fixture):
    _,doc,_,_=fixture
    extractor=RelayFactExtractor('http://localhost:7863/v1','model','PRIVATE-KEY')
    request=extractor.request(doc)
    payload=json.loads(request['messages'][1]['content'])
    assert set(payload)=={'trusted_context','candidate_schema','blocks_in_document_order','tables','parser_warnings'}
    assert 'PRIVATE-KEY' not in json.dumps(request)
    assert extractor.configuration()['automatic_retries']==0


def test_request_schema_enforces_source_owner_without_mutating_primary(fixture):
    from jsonschema import Draft202012Validator
    _,doc,candidate,_=fixture
    extractor=RelayFactExtractor('http://localhost:7863/v1','model','PRIVATE-KEY')
    before=copy.deepcopy(extractor.schema)
    schema=json.loads(extractor.request(doc)['messages'][1]['content'])['candidate_schema']
    validator=Draft202012Validator(schema)
    assert not validator.is_valid(candidate)  # New requests require the new envelope; legacy publications still validate.
    candidate=copy.deepcopy(candidate)
    candidate.update(schema_version='0.3.0-draft',identity_mode='explicit')
    for entity in candidate['entities']:
        if entity['entity_type']=='Observation':entity['data']['area_expression']=None
    assert validator.is_valid(candidate)
    assert extractor.schema==before
    bad=copy.deepcopy(candidate)
    bad['relations'][0]['sources'][0]['target_field']='component_type'
    assert not validator.is_valid(bad)
    bad=copy.deepcopy(candidate)
    bad['statements'][0]['sources'][0]['target_field']='component_code'
    assert not validator.is_valid(bad)


@pytest.mark.parametrize('text',['{"x":1,"x":2}','{"x":NaN}','{"x":Infinity}'])
def test_strict_decoder(text):
    with pytest.raises(ValueError): strict_json(text)


def test_provider_credential_transport_and_truncation(monkeypatch):
    extractor=RelayFactExtractor('http://localhost:7863/v1','model','PRIVATE-KEY')
    def run(command,**kwargs):
        assert 'PRIVATE-KEY' not in json.dumps(command) and 'PRIVATE-KEY' in kwargs['input']
        return SimpleNamespace(returncode=0,stdout=json.dumps({'choices':[{'finish_reason':'length','message':{'content':'PRIVATE-KEY'}}]})+'\n200',stderr='')
    monkeypatch.setattr('ontology_proj.adapters.fact_extractor.subprocess.run',run)
    result=extractor.extract({'messages':[]})
    assert result['candidate'] is None and 'PRIVATE-KEY' not in json.dumps(result)


@pytest.mark.parametrize('status,body', [('503','PRIVATE-KEY unavailable'), ('200','not JSON PRIVATE-KEY')])
def test_transport_failure_preserves_redacted_response(monkeypatch,status,body):
    extractor=RelayFactExtractor('http://localhost:7863/v1','model','PRIVATE-KEY')
    monkeypatch.setattr('ontology_proj.adapters.fact_extractor.subprocess.run',lambda *a,**k: SimpleNamespace(returncode=0,stdout=body+'\n'+status,stderr=''))
    result=extractor.extract({})
    assert result['error_code']=='MODEL_TRANSPORT_FAILED' and result['candidate'] is None
    assert result['response']['http_status']==status and '[REDACTED]' in result['response']['raw_body']
    assert 'PRIVATE-KEY' not in json.dumps(result)


def test_missing_area_cannot_claim_exact_measurement(fixture):
    _,doc,candidate,validator=fixture
    observation=next(e for e in candidate['entities'] if e['entity_type']=='Observation')
    observation['data']['area_original_value']=None
    observation['data']['area_is_approximate']=False
    result=validator.validate(candidate,doc)
    assert result['records'] is None
    assert 'AREA_APPROXIMATION_WITHOUT_VALUE' in {d['code'] for d in result['diagnostics']}


def test_real_first_pass_missing_area_regression():
    suite=ROOT/'specs/002-ingestion-foundation/results/s2.2/baseline-1.1'
    if not (suite/'results/missing-area-01/candidate.json').exists(): pytest.skip('Saved live result not present')
    doc=json.loads((suite/'missing-area-normalized.json').read_text())
    candidate=json.loads((suite/'results/missing-area-01/candidate.json').read_text())
    records=json.loads((suite/'results/missing-area-01/records.json').read_text())
    validator=BusinessValidator()
    assert validator.validate(candidate,doc)['records'] is None
    assert 'RECORD_AREA_APPROXIMATION_WITHOUT_VALUE' in {d['code'] for d in validator.validate_records(records,doc)}


def test_transport_failure_not_published(fixture):
    store,doc,_,validator=fixture
    class Extractor:
        def configuration(self): return {'fixture':'transport'}
        def request(self,document): return {}
        def extract(self,request): return {'response':{'http_status':'503'},'candidate':None,'error_code':'MODEL_TRANSPORT_FAILED','diagnostics':[]}
    run=ExtractBusiness(store,Extractor(),validator).execute(doc['document_id'])
    assert run['status']=='failed' and run['error_code']=='MODEL_TRANSPORT_FAILED'
    assert store.read_business(doc['document_id']) is None
    assert json.loads((store._run(doc['document_id'],run['run_id'])/'response.json').read_text())['http_status']=='503'


def test_publication_configuration_must_match_hash_verified_diagnostic(fixture):
    store,doc,candidate,validator=fixture
    extractor=SimpleNamespace(configuration=lambda:{'fixture':'original'},request=lambda d:{},extract=lambda r:{'response':{},'candidate':copy.deepcopy(candidate),'diagnostics':[]})
    assert ExtractBusiness(store,extractor,validator).execute(doc['document_id'])['status']=='succeeded'
    path=store._directory(doc['document_id'])/'business-manifest.json'
    manifest=json.loads(path.read_text());manifest['configuration']={'fixture':'changed'};path.write_text(json.dumps(manifest))
    with pytest.raises(FileStoreError) as error:store.read_business(doc['document_id'])
    assert error.value.code=='ARTIFACT_MISMATCH'


def test_records_validation_area_value_mismatch(fixture):
    _, doc, candidate, validator = fixture
    result = validator.validate(candidate, doc)
    assert result['records'] is not None
    records = copy.deepcopy(result['records'])
    obs = records['observations'][0]
    obs['area_value'] = '999'
    diagnostics = validator.validate_records(records, doc)
    codes = {d['code'] for d in diagnostics}
    assert 'RECORD_AREA_INCONSISTENT' in codes


def test_records_validation_missing_original_area_does_not_crash(fixture):
    from ontology_proj.domain.business import validate_records as domain_validate_records
    _, doc, candidate, validator = fixture
    result = validator.validate(candidate, doc)
    assert result['records'] is not None
    records = copy.deepcopy(result['records'])
    obs = records['observations'][0]
    obs['area_value'] = '0.04'
    obs['area_original_value'] = None
    diagnostics = domain_validate_records(records, doc, validator.schema, lambda r: [])
    codes = {d['code'] for d in diagnostics}
    assert 'RECORD_AREA_INCONSISTENT' in codes


def test_records_validation_malformed_original_area_does_not_crash(fixture):
    from ontology_proj.domain.business import validate_records as domain_validate_records
    _, doc, candidate, validator = fixture
    result = validator.validate(candidate, doc)
    assert result['records'] is not None
    records = copy.deepcopy(result['records'])
    obs = records['observations'][0]
    obs['area_value'] = '0.04'
    obs['area_original_value'] = 'not-a-number'
    diagnostics = domain_validate_records(records, doc, validator.schema, lambda r: [])
    codes = {d['code'] for d in diagnostics}
    assert 'RECORD_AREA_INCONSISTENT' in codes


