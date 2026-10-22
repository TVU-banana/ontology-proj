"""Authorized one-shot real local OCR; no network/providers/graph."""
import hashlib,json,os,socket,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'specs/013-ocr-routing/results/real-recognition-20261013-01'
DATA=ROOT/'data/ocr-real-recognition-20261013-01'
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def main():
 DATA.mkdir(exist_ok=False)
 for k in ('HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','HF_HUB_DISABLE_TELEMETRY'):os.environ[k]='1'
 attempts=[]
 def deny(*a,**k):attempts.append('network');raise RuntimeError('Local OCR forbids network')
 socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny;socket.getaddrinfo=deny
 from ontology_proj.settings import Settings
 from ontology_proj.bootstrap import build_parser,build_local_parsing
 from ontology_proj.application.dto import ImportContext
 from ontology_proj.domain.source import SourceKind,DocumentKind
 inputs=json.loads((ROOT/'specs/013-ocr-routing/results/real-input-preparation-20261013-01/inputs.json').read_text())['inputs']
 settings=Settings(docling_artifacts_path=ROOT/'data/s23-runtime/models',docling_ocr_models_path=ROOT/'data/ocr-assets-20261013/models')
 parser=build_parser(settings);receipt={'results':[],'network_attempts':attempts,'model_generation_calls':0,'embedding_calls':0,'graph_writes':0,'service_started':False}
 save(OUT/'before.json',{'configuration':parser.configuration(),'inputs':inputs,'expected_facts_sha256':hashlib.sha256((ROOT/'specs/013-ocr-routing/results/real-input-preparation-20261013-01/expectations.md').read_bytes()).hexdigest()})
 for key,entry in zip(['xiaohu','datong'],inputs):
  path=Path(entry['output_file']);assert hashlib.sha256(path.read_bytes()).hexdigest()==entry['sha256']
  service=build_local_parsing(Settings(data_root=DATA/key,docling_artifacts_path=settings.docling_artifacts_path,docling_ocr_models_path=settings.docling_ocr_models_path),parser=parser)
  source=service.prepare(path.read_bytes(),path.name,ImportContext(DocumentKind.INSPECTION_REPORT,SourceKind.REAL,None)).source
  start=time.perf_counter();result=service.process(source.document_id);save(OUT/(key+'-operation.json'),result)
  row={'name':key,'document_id':source.document_id,'status':result['operation']['status'],'error_code':result['operation']['error_code'],'seconds':round(time.perf_counter()-start,3),'ocr':result['operation'].get('ocr'),'input_unchanged':hashlib.sha256(path.read_bytes()).hexdigest()==entry['sha256'],'runtime':str(DATA/key)}
  if row['status'] in ('succeeded','succeeded_with_warnings'):
   normalized=service.store.read_artifact(source.document_id,'normalized');row.update(pages=len(normalized['pages']),blocks=len(normalized['blocks']),tables=len(normalized['tables']),warnings=normalized['warnings'])
   text=[]
   for page in normalized['pages']:
    n=page['page_no'];text.append(f'## PDF物理页 {n}\n')
    text.extend(b['text']+'\n' for b in normalized['blocks'] if any(loc['page_no']==n for loc in b['locations']))
   (OUT/(key+'-text.md')).write_text('# '+path.stem+'：实际解析文字\n\n原始识别结果，不人工修正。source_kind=real；非业务抽取。\n\n'+'\n'.join(text))
  receipt['results'].append(row);save(OUT/'result.json',receipt)
  print(key,row['status'],row['seconds'],flush=True)
 save(OUT/'result.json',receipt)
 return 0 if all(r['status'] in ('succeeded','succeeded_with_warnings') for r in receipt['results']) and not attempts else 1
if __name__=='__main__':raise SystemExit(main())
