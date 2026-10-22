"""Verify graph evidence against immutable published parse artifacts."""
from math import isfinite
from ontology_proj.application.files import FileStoreError


class PublishedEvidenceReader:
    def __init__(self, queries, store):self.queries=queries;self.store=store

    def document(self, document_id):
        self.store.read_pdf(document_id)
        self.store.read_artifact(document_id, "normalized")
        return self.store.read_source(document_id)

    def read(self, evidence_id):
        raw=self.queries.evidence(evidence_id);e=dict(raw['evidence']);doc=raw['document'];id=doc['document_id']
        normalized=self.store.read_artifact(id,'normalized');source=self.store.read_source(id)
        self.store.read_pdf(id)
        block=next((b for b in normalized['blocks'] if b['block_id']==e['block_id']),None)
        def mismatch():raise FileStoreError('EVIDENCE_QUOTE_MISMATCH','Evidence does not match published source')
        if block is None or e['document_id']!=id or raw['block_text']!=block['text'] or e.get('source_ref')!=block.get('source_ref'):mismatch()
        span=e.get('charspan');quote=e.get('quote')
        if not isinstance(span,list) or len(span)!=2 or any(type(i)is not int for i in span) or not 0<=span[0]<span[1]<=len(block['text']) or block['text'][span[0]:span[1]]!=quote:mismatch()
        locations=block['locations'];page_no=e.get('page_no');bbox=e.get('bbox');warnings=[]
        page=next((p for p in normalized['pages'] if p['page_no']==page_no),None)
        if page_no is not None and page is None:mismatch()
        if page_no is not None and not any(l['page_no']==page_no for l in locations):mismatch()
        if bbox is not None:
            valid=isinstance(bbox,list) and len(bbox)==4 and all(type(v)in(int,float) and isfinite(v) and 0<=v<=1 for v in bbox) and bbox[0]<bbox[2] and bbox[1]<bbox[3]
            if not valid or not any(l['page_no']==page_no and isinstance(l.get('bbox'),list) and len(l['bbox'])==4 and all(abs(a-b)<=0.000001 for a,b in zip(l['bbox'],bbox)) for l in locations):mismatch()
        if page is None:warnings.append('Page location unavailable; original text only')
        elif bbox is None:warnings.append('Region unavailable; page and original text only')
        e.setdefault('page_no',None);e.setdefault('bbox',None)
        return {'evidence':e,'block_text':block['text'],'document':{'document_id':id,'filename':source.original_filename,'source_kind':normalized['source_kind']},'records':raw['records'],'page_geometry':page,'pdf_url':f'/api/documents/{id}/file','warnings':warnings}
