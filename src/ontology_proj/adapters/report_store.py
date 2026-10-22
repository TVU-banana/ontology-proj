"""Atomic report artifacts with strict schema, stable keys and verified hashes."""
import hashlib
import json
import os
from pathlib import Path
from importlib.resources import files
from uuid import uuid4
from jsonschema import Draft202012Validator
from ontology_proj.application.files import FileStoreError
from ontology_proj.application.report import identity, render

class LocalReportStore:
    def __init__(self,root):
        self.root=Path(root).resolve()/'reports'
        self.schema=json.loads(files('ontology_proj.resources').joinpath('report.schema.json').read_text())
        self.validator=Draft202012Validator(self.schema)

    def directory(self,rid):
        identity(rid);directory=self.root/rid
        if self.root.is_symlink() or directory.is_symlink():raise FileStoreError('UNSAFE_STORAGE_PATH','Report path is a symlink')
        return directory

    def write(self,rid,name,data):
        directory=self.directory(rid);target=directory/name;temporary=directory/(name+'.'+uuid4().hex+'.tmp')
        try:
            directory.mkdir(parents=True,exist_ok=True)
            if target.exists() or target.is_symlink():raise FileStoreError('REPORT_ARTIFACT_EXISTS','Report artifacts are immutable')
            with temporary.open('xb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,target)
        except OSError:raise FileStoreError('FILE_STORE_UNAVAILABLE','Report artifacts cannot be saved') from None
        finally:temporary.unlink(missing_ok=True)

    def save_input(self,rid,request):self.write(rid,'input.json',json.dumps(request,ensure_ascii=False,indent=2).encode())

    def validate(self,report):
        if not self.validator.is_valid(report):raise FileStoreError('GENERATION_VALIDATION_FAILED','Report schema failed')

    def save(self,report,markdown):
        self.validate(report);rid=report['report_id']
        if markdown!=render(report):raise FileStoreError('GENERATION_VALIDATION_FAILED','Markdown differs from report')
        payload={'report.json':json.dumps(report,ensure_ascii=False,indent=2).encode(),'report.md':markdown.encode()}
        manifest={name:hashlib.sha256(data).hexdigest() for name,data in payload.items()}
        for name,data in payload.items():self.write(rid,name,data)
        self.write(rid,'manifest.json',json.dumps(manifest).encode())
        return manifest

    def read(self,rid):
        directory=self.directory(rid)
        try:
            for name in ('manifest.json','report.json','report.md'):
                if (directory/name).is_symlink():raise FileStoreError('UNSAFE_STORAGE_PATH','Report artifact is a symlink')
            manifest=json.loads((directory/'manifest.json').read_bytes())
            if not isinstance(manifest,dict) or set(manifest)!={'report.json','report.md'}:raise ValueError()
            payload={name:(directory/name).read_bytes() for name in manifest}
            if any(hashlib.sha256(value).hexdigest()!=manifest[name] for name,value in payload.items()):raise FileStoreError('ARTIFACT_MISMATCH','Report hash mismatch')
            report=json.loads(payload['report.json']);markdown=payload['report.md'].decode()
            self.validate(report)
            if report['report_id']!=rid:raise FileStoreError('ARTIFACT_MISMATCH','Report data or Markdown differs')
            return report,markdown,manifest
        except FileNotFoundError:raise FileStoreError('ARTIFACT_MISSING','Complete report artifacts are unavailable; create a new report if generation did not finish') from None
        except (ValueError,UnicodeError):raise FileStoreError('ARTIFACT_MISMATCH','Report files are invalid') from None
        except OSError:raise FileStoreError('FILE_STORE_UNAVAILABLE','Report artifacts cannot be read') from None
