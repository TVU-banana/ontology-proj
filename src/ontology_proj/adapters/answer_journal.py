"""Per-question atomic artifacts, no business/graph mutation."""
import json
import os
from pathlib import Path
from uuid import UUID,uuid4
from ontology_proj.application.files import FileStoreError

class LocalAnswerJournal:
    def __init__(self,root):self.root=Path(root).resolve()/'answers'

    def save(self,question_id,phase,value):
        if str(UUID(question_id))!=question_id or phase not in ('input','context','request','response','validation','answer'):
            raise FileStoreError('INVALID_REQUEST','Invalid answer artifact key')
        directory=self.root/question_id;target=directory/(phase+'.json');temporary=directory/(phase+'.'+uuid4().hex+'.tmp')
        try:
            if self.root.is_symlink() or directory.is_symlink():raise FileStoreError('UNSAFE_STORAGE_PATH','Answer directory is a symlink')
            directory.mkdir(parents=True,exist_ok=True)
            if target.exists() or target.is_symlink():raise FileStoreError('ANSWER_ARTIFACT_EXISTS','Answer artifact already exists')
            with temporary.open('x',encoding='utf-8') as stream:
                json.dump(value,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,target)
        except OSError:
            raise FileStoreError('FILE_STORE_UNAVAILABLE','Answer artifacts cannot be saved') from None
        finally:
            temporary.unlink(missing_ok=True)
