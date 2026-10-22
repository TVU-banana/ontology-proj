from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path

import pytest

from ontology_proj.adapters.file_store import LocalFileStore
from ontology_proj.application.dto import ImportContext
from ontology_proj.application.files import FileStoreError
from ontology_proj.domain.source import DocumentKind, SourceKind


CONTEXT = ImportContext(DocumentKind.INSPECTION_REPORT, SourceKind.SYNTHETIC, "B-DEMO-001")
PDF = b"%PDF-1.7\nfile-store fixture (not a parser test)\n"


def test_source_saved_and_read_after_new_adapter(tmp_path):
    saved = LocalFileStore(tmp_path).save_source(PDF, "../../outside.pdf", CONTEXT)
    assert not saved.duplicate
    digest = hashlib.sha256(PDF).hexdigest()
    assert saved.source.document_id == "doc_" + digest
    assert saved.source.pdf_key == f"documents/doc_{digest}/source.pdf"
    assert not (tmp_path.parent / "outside.pdf").exists()
    store = LocalFileStore(tmp_path)
    assert store.read_source(saved.source.document_id) == saved.source
    assert store.read_pdf(saved.source.document_id) == PDF
    payload = json.loads((tmp_path / saved.source.pdf_key).with_name("source.json").read_text())
    assert payload["source_kind"] == "synthetic" and payload["bytes"] == len(PDF)
    assert "processing_status" not in payload and "observed_at" not in payload
    assert not list(tmp_path.rglob("*.tmp"))


def test_duplicate_retains_metadata_and_never_writes(tmp_path, monkeypatch):
    store = LocalFileStore(tmp_path)
    first = store.save_source(PDF, "first.pdf", CONTEXT)
    def forbidden(*args):
        pytest.fail("duplicate must not rewrite artifacts")
    monkeypatch.setattr(store, "_atomic_write", forbidden)
    duplicate = store.save_source(PDF, "second.pdf", CONTEXT)
    assert duplicate.duplicate and duplicate.source == first.source


@pytest.mark.parametrize("context", [
    ImportContext(DocumentKind.OTHER, SourceKind.SYNTHETIC, "B-DEMO-001"),
    ImportContext(DocumentKind.INSPECTION_REPORT, SourceKind.REAL, "B-DEMO-001"),
    ImportContext(DocumentKind.INSPECTION_REPORT, SourceKind.SYNTHETIC, None),
])
def test_conflicting_context_does_not_overwrite(tmp_path, context):
    store = LocalFileStore(tmp_path)
    first = store.save_source(PDF, "first.pdf", CONTEXT)
    metadata = tmp_path / first.source.pdf_key
    before = metadata.with_name("source.json").read_bytes()
    with pytest.raises(FileStoreError) as error:
        store.save_source(PDF, "conflict.pdf", context)
    assert error.value.code == "SOURCE_CONTEXT_CONFLICT"
    assert store.read_pdf(first.source.document_id) == PDF
    assert metadata.with_name("source.json").read_bytes() == before


@pytest.mark.parametrize("document_id", ["../escape", "/private/tmp/source", "doc_" + "a" * 63, "doc_" + "A" * 64])
def test_arbitrary_path_not_accepted_as_id(tmp_path, document_id):
    with pytest.raises(FileStoreError) as error:
        LocalFileStore(tmp_path).read_pdf(document_id)
    assert error.value.code == "INVALID_DOCUMENT_ID"


@pytest.mark.parametrize("target", ["documents", "document", "source.pdf", "source.json"])
def test_symlinks_rejected_without_touching_target(tmp_path, target):
    root = tmp_path / "data"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    protected = outside / "protected"
    protected.write_bytes(b"unchanged")
    digest = hashlib.sha256(PDF).hexdigest()
    directory = root / "documents" / ("doc_" + digest)
    if target == "documents":
        (root / "documents").symlink_to(outside, target_is_directory=True)
    elif target == "document":
        directory.parent.mkdir()
        directory.symlink_to(outside, target_is_directory=True)
    else:
        directory.mkdir(parents=True)
        (directory / target).symlink_to(protected)
    with pytest.raises(FileStoreError) as error:
        LocalFileStore(root).save_source(PDF, "source.pdf", CONTEXT)
    assert error.value.code == "UNSAFE_STORAGE_PATH"
    assert protected.read_bytes() == b"unchanged"


@pytest.mark.parametrize("corruption", ["pdf", "metadata", "key", "extra", "context", "timestamp", "missing"])
def test_incomplete_or_tampered_pair_never_reused(tmp_path, corruption):
    store = LocalFileStore(tmp_path)
    source = store.save_source(PDF, "source.pdf", CONTEXT).source
    pdf = tmp_path / source.pdf_key
    metadata = pdf.with_name("source.json")
    if corruption == "pdf":
        pdf.write_bytes(PDF + b"tampered")
    elif corruption == "missing":
        metadata.unlink()
    elif corruption == "metadata":
        metadata.write_text("{broken")
    else:
        payload = json.loads(metadata.read_text())
        if corruption == "key": payload["pdf_key"] = "../../outside.pdf"
        if corruption == "extra": payload["processing_status"] = "graph_ready"
        if corruption == "context": payload["source_kind"] = "guess"
        if corruption == "timestamp": payload["created_at"] = "2026-10-11T00:00:00"
        metadata.write_text(json.dumps(payload))
    with pytest.raises(FileStoreError) as error:
        store.save_source(PDF, "again.pdf", CONTEXT)
    assert error.value.code == ("ARTIFACT_MISSING" if corruption == "missing" else "ARTIFACT_MISMATCH")


def test_interrupted_source_publication_can_be_retried(tmp_path, monkeypatch):
    import ontology_proj.adapters.file_store as adapter
    real_replace = adapter.os.replace
    def fail_metadata(src, dst):
        if Path(dst).name == "source.json":
            raise OSError("secret credential and private path")
        real_replace(src, dst)
    monkeypatch.setattr(adapter.os, "replace", fail_metadata)
    store = LocalFileStore(tmp_path)
    with pytest.raises(FileStoreError) as error:
        store.save_source(PDF, "source.pdf", CONTEXT)
    assert error.value.code == "FILE_STORE_UNAVAILABLE" and error.value.retryable
    assert "secret" not in str(error.value) and str(tmp_path) not in str(error.value)
    assert not list(tmp_path.rglob("*.tmp"))
    assert not list(tmp_path.rglob("source.json"))
    assert not list((tmp_path / 'documents').glob('doc_*'))
    monkeypatch.setattr(adapter.os, 'replace', real_replace)
    assert not store.save_source(PDF, "source.pdf", CONTEXT).duplicate


def test_abandoned_staging_does_not_block_or_publish_source(tmp_path):
    staging = tmp_path / 'documents' / '.source-publish-abandoned'
    staging.mkdir(parents=True)
    (staging / 'source.pdf').write_bytes(PDF)
    saved = LocalFileStore(tmp_path).save_source(PDF, 'source.pdf', CONTEXT)
    assert not saved.duplicate
    assert LocalFileStore(tmp_path).read_pdf(saved.source.document_id) == PDF
    assert (staging / 'source.pdf').read_bytes() == PDF


def test_final_directory_publish_failure_is_retryable(tmp_path, monkeypatch):
    import ontology_proj.adapters.file_store as adapter
    rename = adapter.os.rename
    def fail(*args):raise OSError('injected publication failure')
    monkeypatch.setattr(adapter.os, 'rename', fail)
    with pytest.raises(FileStoreError):LocalFileStore(tmp_path).save_source(PDF, 'source.pdf', CONTEXT)
    assert not list((tmp_path / 'documents').iterdir())
    monkeypatch.setattr(adapter.os, 'rename', rename)
    assert not LocalFileStore(tmp_path).save_source(PDF, 'source.pdf', CONTEXT).duplicate


def test_single_adapter_serializes_parallel_submissions(tmp_path):
    store = LocalFileStore(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.save_source(PDF, "file.pdf", CONTEXT), range(4)))
    assert sum(not item.duplicate for item in results) == 1
    assert len({item.source.document_id for item in results}) == 1


@pytest.mark.parametrize("data,name,context", [(b"", "file.pdf", CONTEXT), (PDF, "", CONTEXT), (PDF, "file.pdf", None)])
def test_invalid_input_fails_before_creating_files(tmp_path, data, name, context):
    with pytest.raises(FileStoreError) as error:
        LocalFileStore(tmp_path).save_source(data, name, context)
    assert error.value.code == "INVALID_SOURCE_INPUT"
    assert not list(tmp_path.iterdir())
