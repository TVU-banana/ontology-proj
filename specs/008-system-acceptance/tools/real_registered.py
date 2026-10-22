"""Fresh registered real PDF runs; immutable first calls, approved budget two."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import time
from uuid import uuid4
from ontology_proj.settings import Settings
from ontology_proj.bootstrap import build_extractor, build_validator
from ontology_proj.adapters.ingestion_store import IngestionStore, ProcessLease
from ontology_proj.application.business_extraction import ExtractBusiness
from ontology_proj.application.dto import ImportContext
from ontology_proj.domain.source import SourceKind, DocumentKind
from ontology_proj.domain.registration import ManualRegistration
from ontology_proj.domain.business import hash_id
from ontology_proj.adapters.fact_extractor import canonical

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT/'data/s52-local-shengli-20261012-01'
API = 'http://relay.example.invalid:7863/v1'
MODEL = 'cn:deepseek-v4.1-flash'


def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def write(p,value):
    p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('x') as f:f.write(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def paths(run):
    return ROOT/f'specs/008-system-acceptance/results/real-shengli-20261012-{run:02d}',ROOT/f'data/s52-real-shengli-20261012-{run:02d}'


def registry(document):
    blocks={b['block_id'].split(':')[-1]:b for b in document['blocks']}
    bridge='B-MANUAL-SHENGLI-29FC28B2'
    def source(number,quote):
        b=blocks[f'{number:06d}'];assert b['text'].count(quote)==1
        return {'block_id':b['block_id'],'quote':quote}
    def scope(number,start,end=None):
        b=blocks[f'{number:06d}'];text=b['text'];a=text.index(start);z=text.index(end,a) if end else len(text)
        return {'block_id':b['block_id'],'charspan':[a,z],'quote':text[a:z]}
    entries=[{'kind':'Bridge','record_id':bridge,'original_code':None,'names':['平湖市胜利大桥','胜利大桥'],
              'sources':[source(2,'平湖市胜利大桥')]}]
    definitions=[('C-MANUAL-DECK','桥面铺装','deck_pavement',349,'桥面铺装',['3跨'],[scope(350,'经检查')],[scope(363,'经检查')]),
                 ('C-MANUAL-3-1-WEB','3-1#左侧腹板',None,434,'3-1#左侧腹板',['3-1#左侧腹板'],[scope(434,'3-1#左侧腹板','，')],[]),
                 ('C-MANUAL-3-1-CANTILEVER','3-1#挑臂',None,470,'3-1#挑臂',['3-1#挑臂'],[scope(434,'挑臂破损露筋','；')],[])]
    for key,name,ctype,number,quote,locations,observations,statements in definitions:
        entries.append({'kind':'Component','registration_key':key,'record_id':hash_id('cmp_',[bridge,key]),'parent_id':bridge,
                        'original_code':None,'names':[name],'allowed_component_type':ctype,
                        'defect_location_markers':locations,'sources':[source(number,quote)],
                        'observation_scopes':observations,'statement_scopes':statements})
    return {'version':'manual-registration-1','status':'confirmed_by_user','document_id':document['document_id'],
            'authority':'User explicitly approved controlled registration; professional system review still not_run',
            'entries':entries,'unusable_source_refs':['#/tables/13/data/table_cells/277'],
            'quality_boundaries':[{'pages':[34,35],'status':'untrusted headers/column correspondence; known numeric source unusable'},
                                  {'pages':[18,19],'status':'native continuation not established; only explicitly registered source spans bound'},
                                  {'pages':[55,56,57,58,59,60],'status':'OCR not run, no text coverage'}]}


def prepare(run):
    out,data=paths(run);out.mkdir(parents=True,exist_ok=True);data.mkdir(parents=True,exist_ok=True)
    assert not (out/'preparation.json').exists()
    old=[ROOT/p for p in subprocess.check_output(['git','ls-files','-z']).decode().split('\0') if p]
    for folder in ['s52-local-shengli-20261012-01','s52-real-shengli-20261012-01','s52-real-shengli-20261012-02',
                   's52-proofread-20261012-01','s52-outbound-plan-20261012-01','s52-table-trust-20261012-01','s23-runtime','s52-system-20261012-01']:
        old.extend(p for p in (ROOT/'data'/folder).rglob('*') if p.is_file() and not p.is_symlink() and '__pycache__' not in p.parts)
    write(data/'protected-files.json',{str(p.relative_to(ROOT)):sha(p) for p in old})
    document=json.loads((SOURCE/'parsed/normalized.json').read_text());reg=registry(document)
    document['import_context']['bridge_id']=reg['entries'][0]['record_id'];ManualRegistration(reg,document)
    reg_path=data/'runtime/registrations/shengli.json';write(reg_path,reg)
    oldsettings=Settings(_env_file=ROOT/'data/s23-runtime/.env.s32')
    assert oldsettings.llm_base_url.rstrip('/')==API and oldsettings.llm_model==MODEL
    password=secrets.token_urlsafe(24)
    settings=oldsettings.model_copy(update={'data_root':data/'runtime','app_port':19100,'actor_id':f's52_real_shengli_{run}',
          'identity_registry_path':reg_path,'identity_registry_sha256':sha(reg_path),
          'neo4j_uri':'bolt://127.0.0.1:19687','neo4j_password':password})
    private=data/'private';private.mkdir(exist_ok=True)
    lines=[]
    for key,value in settings.model_dump().items():
        if hasattr(value,'get_secret_value'):value=value.get_secret_value()
        if value is None:continue
        lines.append(key.upper()+'='+json.dumps(str(value),ensure_ascii=False))
    env=private/'.env.app';env.write_text('\n'.join(lines)+'\n');env.chmod(0o600)
    graph_env=private/'.env.neo4j';graph_env.write_text('NEO4J_AUTH=neo4j/'+password+'\n');graph_env.chmod(0o600)
    settings=Settings(_env_file=env)
    store=IngestionStore(settings.data_root);context=ImportContext(DocumentKind.INSPECTION_REPORT,SourceKind.REAL,reg['entries'][0]['record_id'])
    source=store.save_source((SOURCE/'input/shengli-original.pdf').read_bytes(),'shengli-original.pdf',context).source
    config=json.loads((ROOT/'specs/008-system-acceptance/results/local-shengli-20261012-01/parser-result.json').read_text())
    now=datetime.now(timezone.utc).isoformat();op={'operation_id':str(uuid4()),'document_id':source.document_id,'scope':'local_parse',
          'status':'running','stage':'publishing','error_code':None,'created_at':now,'updated_at':now,
          'configuration':config['configuration'],'artifact_keys':[]}
    store.save_operation(op);store.save_artifact(source.document_id,'docling',json.loads((SOURCE/'parsed/docling.json').read_text()))
    store.save_artifact(source.document_id,'normalized',document);store.publish(source.document_id,op['operation_id'],config['configuration'],config['versions'])
    op.update(status='succeeded_with_warnings',stage='published');store.save_operation(op)
    adapter=build_extractor(settings);request=adapter.request(document);write(data/'extraction-request.json',request);write(data/'source-aliases.json',adapter.aliases)
    payload=json.loads(request['messages'][1]['content']);rows=payload['blocks_in_document_order']
    assert len(rows)==len(document['blocks'])==3045 and len(payload['tables'])==34
    for oldb,row in zip(document['blocks'],rows):
        decoded=dict(zip(payload['block_fields'],row));assert adapter.aliases[decoded['block_id']]==oldb['block_id']
        assert decoded['text']==oldb['text'] and decoded['original_text']==oldb.get('original_text')
        for field in ('row','column','row_span','column_span','column_header','row_header','table_id'):assert decoded[field]==oldb.get(field)
    assert len(set(adapter.aliases.values()))==3045 and len(adapter.prohibited)==1
    assert oldsettings.llm_api_key.get_secret_value() not in canonical(request)
    info={'document_id':source.document_id,'all_blocks_preserved':3045,'all_tables_preserved':34,
          'only_normalized_metadata_change':'trusted import_context.bridge_id, original blocks/table/warnings unchanged',
          'schema_version':'0.2.0-draft','manual_registered_objects':4,'maximum_extraction_calls_this_run':1,
          'total_new_registered_extraction_budget':2,'canonical_request_bytes':len(canonical(request).encode()),
          'max_tokens':64000,'original_code_null_preserved':True,'golden_read_by_builder':False,
          'new_docling_calls':0,'api':API,'model':MODEL,'upstream_revision':'not verified','professional_review':'not_run'}
    write(out/'preparation.json',info)
    frozen=[p for p in (ROOT/'src').rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    frozen += [Path(__file__),ROOT/'specs/001-business-extraction/business.schema.json',ROOT/'specs/009-controlled-registration/plan.md',reg_path,data/'source-aliases.json']
    write(out/'freeze.json',{'files':{str(p.relative_to(ROOT)):sha(p) for p in frozen},'request_canonical_sha256':hashlib.sha256(canonical(request).encode()).hexdigest()})
    print(json.dumps(info,ensure_ascii=False))


def extract(run):
    out,data=paths(run);assert not (out/'extraction-result.json').exists()
    settings=Settings(_env_file=data/'private/.env.app');store=IngestionStore(settings.data_root);adapter=build_extractor(settings)
    original=adapter.extract;calls=[]
    def guarded(request):
        frozen=json.loads((out/'freeze.json').read_text())
        assert hashlib.sha256(canonical(request).encode()).hexdigest()==frozen['request_canonical_sha256']
        for name,digest in frozen['files'].items():assert sha(ROOT/name)==digest
        write(data/'call-claim.json',{'maximum':1,'approved_total_new_calls':2,'run':run})
        start=time.monotonic();result=original(request);response=result.get('response',{})
        write(out/'provider-result.json',{'elapsed_seconds':round(time.monotonic()-start,3),'model':response.get('model'),
              'usage':response.get('usage'),'finish_reason':(response.get('choices') or [{}])[0].get('finish_reason'),
              'diagnostics':result.get('diagnostics'),'error_code':result.get('error_code')})
        calls.append(1);return result
    adapter.extract=guarded;lease=ProcessLease(store);lease.acquire()
    try:
        result=ExtractBusiness(store,adapter,build_validator(settings)).execute(json.loads((out/'preparation.json').read_text())['document_id'])
        write(out/'extraction-result.json',result);print(json.dumps(result,ensure_ascii=False))
    finally:lease.release()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['prepare','extract']);parser.add_argument('--run',type=int,choices=[3,4],required=True)
    args=parser.parse_args();prepare(args.run) if args.mode=='prepare' else extract(args.run)
