from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ontology_proj.adapters.document_normalizer import DoclingNormalizer
from ontology_proj.adapters.local_processing_store import LocalProcessingStore
from ontology_proj.application.dto import ImportContext
from ontology_proj.application.files import FileStoreError
from ontology_proj.application.local_parsing import LocalParsingService
from ontology_proj.application.parsing import ParseOutcome, ParseStatus, ParserDiagnostic
from ontology_proj.domain.source import DocumentKind, SourceKind

ROOT = Path(__file__).resolve().parents[2]
PDF = (ROOT / 'samples/synthetic/bridge-demo-001/input/bridge-demo-001.pdf').read_bytes()
NATIVE = json.loads((ROOT / 'experiments/docling-parse-001/results/docling.json').read_text())
CONTEXT = ImportContext(DocumentKind.INSPECTION_REPORT, SourceKind.SYNTHETIC, 'B-DEMO-001')


class SnapshotParser:
    # Explicit test double: read saved native document, no actual Docling conversion.
    calls = 0
    status = ParseStatus.SUCCESS
    def configuration(self): return {'fixture': 'saved-native', 'threads': 4}
    def parse(self, source_path, document_id):
        self.calls += 1
        return ParseOutcome(document_id, self.status, NATIVE if self.status != ParseStatus.FAILED else None,
                            (ParserDiagnostic('PARSER_PARTIAL_RESULT', 'Partial fixture'),) if self.status == ParseStatus.PARTIAL else (),
                            {'fixture': 'saved'}, 0)


@pytest.fixture
def setup(tmp_path):
    store = LocalProcessingStore(tmp_path)
    parser = SnapshotParser()
    service = LocalParsingService(store, parser, DoclingNormalizer())
    source = service.prepare(PDF, 'source.pdf', CONTEXT).source
    return store, parser, service, source


def test_success_manifest_and_duplicate_do_not_parse_again(setup):
    store, parser, service, source = setup
    first = service.process(source.document_id)
    assert first['operation']['status'] == 'succeeded_with_warnings'
    manifest = service.read(source.document_id)
    assert set(manifest['artifacts']) == {'source.pdf', 'source.json', 'docling.json', 'normalized.json'}
    assert manifest['operation_id'] == first['operation_id']
    assert store.read_artifact(source.document_id, 'normalized')['document_id'] == source.document_id
    assert service.prepare(PDF, 'renamed.pdf', CONTEXT).duplicate
    assert service.process(source.document_id)['reused']
    assert parser.calls == 1


@pytest.mark.parametrize('status', [ParseStatus.PARTIAL, ParseStatus.FAILED])
def test_failure_preserves_response_and_requires_explicit_retry(setup, status):
    store, parser, service, source = setup
    parser.status = status
    first = service.process(source.document_id)
    assert first['operation']['status'] == 'failed'
    assert store.read_published(source.document_id) is None
    if status == ParseStatus.PARTIAL:
        assert (store._directory(source.document_id) / 'docling.json').exists()
    with pytest.raises(FileStoreError) as failure: service.process(source.document_id)
    assert failure.value.code == 'EXPLICIT_RETRY_REQUIRED'
    assert parser.calls == 1
    parser.status = ParseStatus.SUCCESS
    retried = service.process(source.document_id, retry=True)
    assert retried['operation_id'] != first['operation_id']
    assert store.read_operation(first['operation_id'])['status'] == 'failed'
    if status == ParseStatus.PARTIAL:
        assert (store._root / 'operations' / first['operation_id'] / 'docling.json').exists()
    assert parser.calls == 2


@pytest.mark.parametrize('artifact', ['docling', 'normalized'])
def test_artifact_write_failure_is_not_published_and_is_sanitized(setup, monkeypatch, artifact):
    store, parser, service, source = setup
    write = store.save_artifact
    def fail(document_id, name, value):
        if name == artifact: raise OSError('private path and key')
        return write(document_id, name, value)
    monkeypatch.setattr(store, 'save_artifact', fail)
    result = service.process(source.document_id)
    assert result['operation']['status'] == 'failed'
    assert result['operation']['error_code'] == 'LOCAL_PROCESS_FAILED'
    assert 'private' not in str(result)
    assert store.read_published(source.document_id) is None


def test_manifest_atomic_write_failure_does_not_publish(setup, monkeypatch):
    store, parser, service, source = setup
    original = store._atomic_write
    def fail(path, data):
        if path.name == 'manifest.json': raise OSError('private details')
        original(path, data)
    monkeypatch.setattr(store, '_atomic_write', fail)
    result = service.process(source.document_id)
    assert result['operation']['status'] == 'failed'
    assert store.read_published(source.document_id) is None


def test_post_commit_diagnostic_failure_recovered_without_reparse(setup, monkeypatch):
    store, parser, service, source = setup
    save = store.save_operation
    def fail(value):
        if value['stage'] == 'published': raise FileStoreError('FILE_STORE_UNAVAILABLE', 'Safe error')
        return save(value)
    monkeypatch.setattr(store, 'save_operation', fail)
    with pytest.raises(FileStoreError) as failure: service.process(source.document_id)
    assert failure.value.code == 'OPERATION_DIAGNOSTIC_PENDING'
    assert store.read_published(source.document_id) is not None
    monkeypatch.setattr(store, 'save_operation', save)
    recovered = service.recover()
    assert len(recovered) == 1
    assert store.read_operation(recovered[0])['status'] == 'succeeded_with_warnings'
    assert service.process(source.document_id)['reused'] and parser.calls == 1


@pytest.mark.parametrize('name', ['docling.json', 'normalized.json', 'source.json'])
def test_published_corruption_is_visible_not_repaired(setup, name):
    store, parser, service, source = setup
    service.process(source.document_id)
    (store._directory(source.document_id) / name).write_text('{}')
    with pytest.raises(FileStoreError) as failure: service.process(source.document_id, retry=True)
    assert failure.value.code == 'ARTIFACT_MISMATCH'
    assert parser.calls == 1


def test_missing_and_not_generated_are_distinct(setup):
    store, parser, service, source = setup
    with pytest.raises(FileStoreError) as first: service.read(source.document_id)
    assert first.value.code == 'ARTIFACT_NOT_GENERATED'
    service.process(source.document_id)
    (store._directory(source.document_id) / 'normalized.json').unlink()
    with pytest.raises(FileStoreError) as second: service.read(source.document_id)
    assert second.value.code == 'ARTIFACT_MISSING'


def test_unfinished_operation_recovery_never_calls_parser(setup):
    store, parser, service, source = setup
    operation = {'operation_id': str(uuid4()), 'document_id': source.document_id, 'scope': 'local_parse',
                 'status': 'running', 'stage': 'parsing', 'error_code': None,
                 'created_at': '2026-10-11T00:00:00+00:00', 'updated_at': '2026-10-11T00:00:00+00:00',
                 'configuration': parser.configuration(), 'artifact_keys': []}
    store.save_operation(operation)
    with pytest.raises(FileStoreError) as active: service.process(source.document_id, retry=True)
    assert active.value.code == 'LOCAL_OPERATION_ACTIVE'
    assert service.recover() == [operation['operation_id']]
    assert store.read_operation(operation['operation_id'])['status'] == 'interrupted'
    assert parser.calls == 0
    with pytest.raises(FileStoreError): service.process(source.document_id)
    assert service.process(source.document_id, retry=True)['operation']['status'] == 'succeeded_with_warnings'


def test_threads_serialize_pipeline_and_reuse_one_publication(setup):
    store, parser, service, source = setup
    with ThreadPoolExecutor(max_workers=4) as pool:
        result = list(pool.map(lambda _: service.process(source.document_id), range(4)))
    assert parser.calls == 1 and sum(not r['reused'] for r in result) == 1


def test_retry_config_conflict_preserves_failed_operation(setup, monkeypatch):
    store, parser, service, source = setup
    parser.status = ParseStatus.FAILED
    first = service.process(source.document_id)
    monkeypatch.setattr(parser, 'configuration', lambda: {'changed': True})
    with pytest.raises(FileStoreError) as failure: service.process(source.document_id, retry=True)
    assert failure.value.code == 'PARSER_CONFIG_CONFLICT'
    assert store.read_operation(first['operation_id'])['status'] == 'failed'


def test_operation_path_and_symlink_cannot_escape(setup, tmp_path):
    store, parser, service, source = setup
    with pytest.raises(FileStoreError): store.read_operation('../../outside')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (tmp_path / 'operations').symlink_to(outside, target_is_directory=True)
    with pytest.raises(FileStoreError) as error: service.process(source.document_id)
    assert error.value.code == 'UNSAFE_STORAGE_PATH'
    assert list(outside.iterdir()) == []


def test_real_pdf_layout_passes_through_service_publication_and_reuse(setup):
    # Parser SDK is a saved-response double; PDF geometry and store are real.
    from ontology_proj.adapters.pdf_layout import read_pdf_layout
    store, parser, service, source = setup
    layout = read_pdf_layout(Path(store.source_path(source.document_id)))
    original = parser.parse
    def parse(path, identity):
        return replace(original(path, identity), pdf_layout=layout)
    parser.parse = parse
    result = service.process(source.document_id)
    assert result['operation']['status'].startswith('succeeded')
    n = store.read_artifact(source.document_id, 'normalized')
    assert n['structure']['revision'] == 'document-structure-1'
    assert n['structure']['source_layout'] == layout
    assert store.read_artifact(source.document_id, 'docling') == NATIVE
    assert service.process(source.document_id)['reused'] and parser.calls == 1
    assert service.read(source.document_id)['document_id'] == source.document_id
