"""Only failed physical page 3: capture engine output before Docling assembly."""
import json,os,socket
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'specs/013-ocr-routing/results/real-diagnosis-20261013-01';DATA=ROOT/'data/ocr-real-diagnosis-20261013-01';DATA.mkdir(exist_ok=False)
for k in ('HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','HF_HUB_DISABLE_TELEMETRY'):os.environ[k]='1'
attempts=[]
def deny(*a,**k):attempts.append('network');raise RuntimeError('network forbidden')
socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny;socket.getaddrinfo=deny
from ontology_proj.adapters import ocr_routing
from ontology_proj.adapters.docling_parser import _quiet_sdk
from PIL import Image
calls=[]
with _quiet_sdk():
 converter=ocr_routing.make_converter(ROOT/'data/s23-runtime/models',4,ROOT/'data/ocr-assets-20261013/models',())
 pipeline=next(iter(converter.initialized_pipelines.values()));engine=pipeline.ocr_model.reader
 steps=engine.run_ocr_steps
 def capture_steps(img,record):
  det,cls,rec,crops=steps(img,record)
  calls[-1]['pre_filter']={'boxes':det.boxes.tolist() if det.boxes is not None else [],'texts':list(rec.txts or []),'scores':[float(s) for s in (rec.scores or [])]}
  Image.fromarray(img).save(DATA/f'preprocessed-{len(calls)}.png')
  return det,cls,rec,crops
 engine.run_ocr_steps=capture_steps
 def reader(img,*a,**k):
  calls.append({'call':len(calls)+1,'image_size':[img.shape[1],img.shape[0]]})
  Image.fromarray(img).save(DATA/f'input-{len(calls)}.png')
  result=engine(img,*a,**k);calls[-1]['final']={'texts':list(result.txts or []),'scores':[float(s) for s in (result.scores or [])],'boxes':result.boxes.tolist() if result.boxes is not None else []}
  return result
 pipeline.ocr_model.reader=reader
 result=converter.convert(ROOT/'output/pdf/小虎一桥-真实扫描报告-公开4页.pdf',page_range=(3,3),raises_on_error=False)
 native=result.document.export_to_dict()
(DATA/'docling.json').write_text(json.dumps(native,ensure_ascii=False,indent=2)+'\n')
receipt={'status':result.status.value,'calls':calls,'route':converter._ontology_ocr_trace,'network_attempts':attempts,'scope':'failed physical page 3 only, no configuration/model changes or original artifact overwrite'}
(OUT/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
print('status',receipt['status'])
for row in calls:
 print('call',row['call'],'pre',row['pre_filter']['texts'],'scores',row['pre_filter']['scores'],'final',row['final']['texts'])
