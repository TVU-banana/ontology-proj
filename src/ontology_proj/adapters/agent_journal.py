"""Atomic per-run Agent artifacts; no business/graph mutation."""
import re
import json
import os
from pathlib import Path
from uuid import UUID,uuid4
from ontology_proj.application.files import FileStoreError

class LocalAgentJournal:
    def __init__(self,root):self.root=Path(root).resolve()/'agents'

    def save(self,run_id,phase,value):
        if str(UUID(run_id))!=run_id or not re.fullmatch(r'input|summary|(?:request|response|tool)-[1-8]',phase):
            raise FileStoreError('INVALID_REQUEST','Invalid agent artifact key')
        directory=self.root/run_id;target=directory/(phase+'.json');temporary=directory/(phase+'.'+uuid4().hex+'.tmp')
        try:
            if self.root.is_symlink() or directory.is_symlink():raise FileStoreError('UNSAFE_STORAGE_PATH','Agent directory is a symlink')
            directory.mkdir(parents=True,exist_ok=True)
            if target.exists() or target.is_symlink():raise FileStoreError('AGENT_ARTIFACT_EXISTS','Agent artifact already exists')
            with temporary.open('x',encoding='utf-8') as stream:
                json.dump(value,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,target)
        except OSError:
            raise FileStoreError('FILE_STORE_UNAVAILABLE','Agent artifacts cannot be saved') from None
        finally:
            temporary.unlink(missing_ok=True)
