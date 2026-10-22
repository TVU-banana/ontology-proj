"""Isolated real CPU OCR and persistence, no providers/graph/network."""
import hashlib
import json
import os
from pathlib import Path
import socket
import time

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / 'data/ocr-verification-20261013-03'
OUT = ROOT / 'specs/013-ocr-routing/results'

def save(p, value):
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

def main():
    DATA.mkdir(parents=True, exist_ok=False)
    (DATA/'input').mkdir()
    # Controlled new input: native page, its rasterized counterpart, blank, picture.
    from pypdf import PdfReader, PdfWriter
    from PIL import Image, ImageDraw
    import pypdfium2 as pdfium
    source = ROOT/'samples/synthetic/bridge-demo-001/input/bridge-demo-001.pdf'
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    pdf = pdfium.PdfDocument(source)
    raster = pdf[0].render(scale=3).to_pil()
    raster.save(DATA/'input/scan.pdf', 'PDF', resolution=216)
    pdf.close()
    photo=Image.new('RGB',(1200,800),'#72a9ce');draw=ImageDraw.Draw(photo)
    draw.polygon([(0,800),(260,330),(520,800)],fill='#487c51')
    draw.ellipse((800,90,920,210),fill='#ffe09a')
    photo.save(DATA/'input/picture.pdf','PDF',resolution=144)
    writer=PdfWriter(); writer.add_page(PdfReader(source).pages[0]);writer.add_page(PdfReader(DATA/'input/scan.pdf').pages[0]);writer.add_blank_page(width=595,height=842);writer.add_page(PdfReader(DATA/'input/picture.pdf').pages[0])
    input_pdf=DATA/'input/mixed.pdf';writer.write(input_pdf)
    for key in ('HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','HF_HUB_DISABLE_TELEMETRY'):os.environ[key]='1'
    attempts=[]
    def deny(*a,**k):attempts.append('network');raise RuntimeError('Network forbidden in OCR verification')
    socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny;socket.getaddrinfo=deny
    from ontology_proj.settings import Settings
    from ontology_proj.bootstrap import build_local_parsing,build_parser
    from ontology_proj.application.dto import ImportContext
    from ontology_proj.domain.source import SourceKind,DocumentKind
    context=ImportContext(DocumentKind.INSPECTION_REPORT,SourceKind.SYNTHETIC,None)
    settings=Settings(data_root=DATA/'runtime',docling_artifacts_path=ROOT/'data/s23-runtime/models',
                      docling_ocr_models_path=ROOT/'data/ocr-assets-20261013/models')
    service=build_local_parsing(settings)
    src=service.prepare(input_pdf.read_bytes(),'ocr-mixed-synthetic.pdf',context).source
    start=time.perf_counter();result=service.process(src.document_id)
    save(DATA/'operation-result.json',result)
    checks=[]
    def check(name,passed):checks.append({'name':name,'passed':bool(passed)})
    check('published',result['operation']['status'] in ('succeeded','succeeded_with_warnings'))
    if checks[-1]['passed']:
        normalized=service.store.read_artifact(src.document_id,'normalized');native=service.store.read_artifact(src.document_id,'docling')
        rows={p['page_no']:p for p in normalized['ocr']['pages']}
        check('four_pages',set(rows)=={1,2,3,4})
        check('native_skip',rows[1]['reader_calls']==0)
        check('scan_ocr',rows[2]['reader_calls']>0 and rows[2]['ocr_cells']>0)
        check('blank_skip',rows[3]['reader_calls']==0)
        check('picture_skip',rows[4]['reader_calls']==0)
        text=' '.join(b['text'] for b in normalized['blocks'] if any(loc['page_no']==2 for loc in b.get('locations', [])))
        # Fixed before conversion; do not derive from OCR output.
        for token in ['桥梁','C-DECK-01','0.04','未见明显病害','m']:
            check('scan_token_'+token,token in text)
        check('native_not_modified','ocr' not in native)
        check('operation_trace_saved',result['operation']['ocr']==normalized['ocr'])
        check('source_unchanged',hashlib.sha256(source.read_bytes()).hexdigest()==source_sha)
        check('bbox_normalized',all(0<=x<=1 for r in rows.values() for box in r['regions'] for x in box))
        save(DATA/'scan-text.json',{'text':text,'rows':list(rows.values())})
    # New isolated parser object; forced scan page tests full-page path, native stays auto.
    force=build_parser(Settings(docling_artifacts_path=settings.docling_artifacts_path,
                      docling_ocr_models_path=settings.docling_ocr_models_path,docling_ocr_force_pages=[2]))
    forced=force.parse(str(input_pdf),'doc_'+hashlib.sha256(input_pdf.read_bytes()).hexdigest())
    save(DATA/'forced-result.json',{'status':forced.status,'ocr':forced.ocr,'diagnostics':[d.code for d in forced.diagnostics]})
    check('force_success',forced.status=='success')
    frows={p['page_no']:p for p in forced.ocr['pages']}
    check('force_page_full',frows[2]['reason']=='forced_page' and frows[2]['regions']==[[0.0,0.0,1.0,1.0]])
    check('force_other_native_skip',frows[1]['reader_calls']==0)
    check('network_zero',not attempts)
    receipt={'checks':checks,'passed':sum(c['passed'] for c in checks),'failed':sum(not c['passed'] for c in checks),
             'seconds':round(time.perf_counter()-start,3),'data_root':str(DATA),'network_attempts':attempts,
             'generation_calls':0,'embedding_calls':0,'graph_writes':0,'boundary':'real local OCR on controlled synthetic variants; no real bridge OCR/professional acceptance'}
    save(OUT/'local-verification.json',receipt); print(json.dumps(receipt,ensure_ascii=False))
    return 0 if not receipt['failed'] else 1

if __name__=='__main__':raise SystemExit(main())
