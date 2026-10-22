"""Inspect failed paragraph crop, not full PDF; compare direction classifier toggle."""
import json,socket
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'specs/013-ocr-routing/results/real-diagnosis-20261013-01'
from ontology_proj.adapters.docling_parser import _quiet_sdk
from ontology_proj.adapters.ocr_routing import OCR_MODELS
attempts=[]
def deny(*a,**k):attempts.append('network');raise RuntimeError('no network')
socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny;socket.getaddrinfo=deny
from rapidocr import RapidOCR
from PIL import Image
import numpy as np
params={f'{k.title()}.model_path':str(ROOT/'data/ocr-assets-20261013/models'/n) for k,(n,_) in OCR_MODELS.items()}
params.update({'EngineConfig.onnxruntime.intra_op_num_threads':4,'EngineConfig.onnxruntime.inter_op_num_threads':4})
rows=[]
with _quiet_sdk():
 engine=RapidOCR(params=params);steps=engine.run_ocr_steps
 def capture(img,record):
  det,cls,rec,crops=steps(img,record)
  rows[-1].update(classification=[[str(x[0]),float(x[1])] for x in (cls.cls_res or [])],pre_filter_texts=list(rec.txts or []),pre_filter_scores=[float(x) for x in (rec.scores or [])])
  return det,cls,rec,crops
 engine.run_ocr_steps=capture
 img=np.array(Image.open(ROOT/'data/ocr-real-diagnosis-20261013-01/input-5.png'))
 for classify in [True,False]:
  rows.append({'use_cls':classify});r=engine(img,use_cls=classify);rows[-1]['final_texts']=list(r.txts or [])
(OUT/'line-comparison.json').write_text(json.dumps({'rows':rows,'network_attempts':attempts,'config_changed_in_application':False},ensure_ascii=False,indent=2)+'\n');print(json.dumps(rows,ensure_ascii=False))
