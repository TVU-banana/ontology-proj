"""Root-local atomic bridge directory and document bindings, never rewrites sources."""
from contextlib import contextmanager
from datetime import datetime, timezone
import copy
import fcntl
import json
from threading import RLock
from uuid import uuid4

from ontology_proj.adapters.local_processing_store import LocalProcessingStore, storage_errors
from ontology_proj.application.files import FileStoreError
from ontology_proj.domain.bridge_identity import VERSION, normalized, official_key, source_facts, decision, snapshot_hash


class BridgeCatalog(LocalProcessingStore):
    def __init__(self, root, actor='local_operator'):
        super().__init__(root)
        self.actor=actor
        self._catalog_lock=RLock()

    @contextmanager
    def transaction(self):
        with self._catalog_lock:
            directory=self._root/'bridge-identities'
            for p in (self._root,directory):
                if p.is_symlink() or p.exists() and not p.is_dir():raise FileStoreError('UNSAFE_STORAGE_PATH','Identity directory is unsafe')
            directory.mkdir(parents=True,exist_ok=True)
            lock=directory/'lock';self._regular(lock)
            with lock.open('a+b') as stream:
                fcntl.flock(stream,fcntl.LOCK_EX)
                path=directory/'state.json';self._regular(path)
                state=self._json(path) if path.exists() else {'version':VERSION,'bridges':[],'documents':{},'audit':[]}
                if state.get('version')!=VERSION:raise FileStoreError('BRIDGE_CATALOG_INVALID','Identity directory version differs')
                before=self._encode(state)
                yield state
                if self._encode(state)!=before:self._atomic_write(path,self._encode(state))

    def _audit(self,state,action,object_id,reason):
        state['audit'].append({'action':action,'object_id':object_id,'reason':reason,'actor':self.actor,'at':datetime.now(timezone.utc).isoformat()})

    @storage_errors
    def adopt_existing(self,record,source_kind):
        # Called only after local publication/evidence verification by the runner.
        if record.get('validation_status')!='passed' or record.get('acceptance_status')!='accepted' or source_kind not in ('real','synthetic') or not record.get('name') or not record.get('bridge_id'):
            raise FileStoreError('BRIDGE_CATALOG_INVALID','Existing bridge is not a verified publication')
        with self.transaction() as state:
            old=next((b for b in state['bridges'] if b['bridge_id']==record['bridge_id']),None)
            if old:
                if normalized(old['name'])!=normalized(record['name']) or old['source_kind']!=source_kind:raise FileStoreError('BRIDGE_CATALOG_INVALID','Existing identity differs from directory')
                return copy.deepcopy(old)
            bridge={'bridge_id':record['bridge_id'],'name':record['name'],'location':None,'source_kind':source_kind,'official_ids':[],
                    'origin':'existing_publication','origin_document_id':record['source_document_id'],'legacy_code':record.get('bridge_code')}
            state['bridges'].append(bridge);self._audit(state,'adopt_existing_bridge',bridge['bridge_id'],'verified_local_publication')
            return copy.deepcopy(bridge)

    @storage_errors
    def list(self):
        with self.transaction() as state:return copy.deepcopy(state['bridges'])

    @storage_errors
    def register(self,value):
        if not isinstance(value,dict) or set(value)-{'name','location','official_code','official_namespace','source_kind'}:raise FileStoreError('INVALID_REQUEST','Unknown bridge fields')
        for k in ('name','location','official_code','official_namespace'):
            if value.get(k) is not None and (not isinstance(value[k],str) or not value[k].strip() or len(value[k])>300 or any(ord(c)<32 for c in value[k])):raise FileStoreError('INVALID_REQUEST','Bridge fields require bounded plain text')
        if not value.get('name') or value.get('source_kind') not in ('real','synthetic') or bool(value.get('official_code'))!=bool(value.get('official_namespace')) or not(value.get('location') or value.get('official_code')):raise FileStoreError('INVALID_REQUEST','Name and complete location or scoped official ID required')
        with self.transaction() as state:
            facts={'ambiguous':False,'values':{k:value.get(k) for k in ('name','location','official_code','official_namespace')}}
            found=decision(state['bridges'],facts,value['source_kind'])
            if found['status']=='resolved':
                bridge=next(b for b in state['bridges'] if b['bridge_id']==found['bridge_id'])
                if value.get('official_code') and not any(official_key(c['namespace'])==official_key(value['official_namespace']) for c in bridge['official_ids']):
                    bridge['official_ids'].append({'namespace':value['official_namespace'],'code':value['official_code'],'origin':'operator_registration'})
                    self._audit(state,'register_official_id',bridge['bridge_id'],'operator_registration')
                return {'bridge':copy.deepcopy(bridge),'duplicate':True}
            if found['reason']!='BRIDGE_MATCH_MISSING':raise FileStoreError('BRIDGE_REGISTRATION_CONFLICT','Registration conflicts with existing bridge')
            bridge={'bridge_id':'brg_'+uuid4().hex,'name':value['name'],'location':value.get('location'),'source_kind':value['source_kind'],
                    'official_ids':[{'namespace':value['official_namespace'],'code':value['official_code'],'origin':'operator_registration'}] if value.get('official_code') else []}
            state['bridges'].append(bridge);self._audit(state,'register_bridge',bridge['bridge_id'],'operator_registration')
            return {'bridge':copy.deepcopy(bridge),'duplicate':False}

    @storage_errors
    def resolve(self,document,selected=None,confirm_details=False):
        if selected is not None and (not isinstance(selected,str) or not selected or len(selected)>128):raise FileStoreError('INVALID_REQUEST','Invalid bridge selection')
        if type(confirm_details) is not bool or confirm_details and not selected:raise FileStoreError('INVALID_REQUEST','Detail confirmation requires explicit selected bridge')
        doc_id=document['document_id'];self._directory(doc_id)
        with self.transaction() as state:
            digest=snapshot_hash(document);old=state['documents'].get(doc_id)
            if old and old['source_sha256']!=digest:raise FileStoreError('BRIDGE_BINDING_SOURCE_CHANGED','Binding source snapshot differs')
            if old and old['status']=='resolved':
                if selected and selected!=old['bridge_id']:raise FileStoreError('BRIDGE_BINDING_IMMUTABLE','Resolved report cannot be reassigned')
                return copy.deepcopy(old)
            facts=source_facts(document);result=decision(state['bridges'],facts,document['source_kind'],selected,confirm_details)
            binding={**result,'version':VERSION,'document_id':doc_id,'source_kind':document['source_kind'],'source_sha256':digest,'facts':facts,'confirmed_details':confirm_details}
            if result['status']=='resolved':
                bridge=next(b for b in state['bridges'] if b['bridge_id']==result['bridge_id'])
                v=facts['values']
                if bridge['location'] is None and v['location']:
                    bridge['location']=v['location'];bridge['location_origin']=doc_id
                if v['official_code'] and v['official_namespace'] and not any(official_key(c['namespace'])==official_key(v['official_namespace']) for c in bridge['official_ids']):
                    bridge['official_ids'].append({'namespace':v['official_namespace'],'code':v['official_code'],'origin':doc_id})
                binding['bridge']=copy.deepcopy(bridge)
            state['documents'][doc_id]=binding;self._audit(state,'resolve_document',doc_id,result['reason'])
            return copy.deepcopy(binding)

    @storage_errors
    def status(self,document):
        with self.transaction() as state:
            value=state['documents'].get(document['document_id'])
            if value and value['source_sha256']!=snapshot_hash(document):raise FileStoreError('BRIDGE_BINDING_SOURCE_CHANGED','Binding source snapshot differs')
            return copy.deepcopy(value) if value else {'status':'not_resolved','reason':'解析后处理或点击确认归属','facts':source_facts(document)}

    @storage_errors
    def binding(self,document):
        with self.transaction() as state:
            value=state['documents'].get(document['document_id'])
            if not value or value['status']!='resolved':raise FileStoreError('BRIDGE_CONFIRMATION_REQUIRED','Bridge ownership needs confirmation')
            if value['source_sha256']!=snapshot_hash(document) or value['source_kind']!=document['source_kind']:raise FileStoreError('BRIDGE_BINDING_SOURCE_CHANGED','Binding source differs')
            matches=[b for b in state['bridges'] if b['bridge_id']==value['bridge_id'] and b['source_kind']==document['source_kind']]
            if len(matches)!=1 or matches[0]['name']!=value['bridge']['name'] or (value['bridge']['location'] is not None and matches[0]['location']!=value['bridge']['location']) or value['facts']!=source_facts(document):raise FileStoreError('BRIDGE_CATALOG_INVALID','Bridge binding is inconsistent')
            # Recheck official constraints against current master; later reports may enrich it.
            check=decision(state['bridges'],value['facts'],document['source_kind'],value['bridge_id'],value.get('confirmed_details',False))
            if check['status']!='resolved':raise FileStoreError('BRIDGE_CATALOG_INVALID','Bridge binding signals conflict')
            return copy.deepcopy(value)
