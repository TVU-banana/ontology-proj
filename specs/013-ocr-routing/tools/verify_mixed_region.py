"""New single-page mixed text-layer fixture, blocked networking, actual Docling."""
import hashlib,json,os,socket
from pathlib import Path
from pypdf import PdfReader,PdfWriter,PageObject
from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'specs/013-ocr-routing/results'
DATA=ROOT/'data/ocr-mixed-region-20261013-01'
DATA.mkdir(exist_ok=False)
source=ROOT/'data/ocr-verification-20261013-03/input/scan.pdf'
page=PdfReader(source).pages[0]
footer=PageObject.create_blank_page(width=float(page.mediabox.width),height=float(page.mediabox.height))
font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
footer[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
stream=DecodedStreamObject();stream.set_data(b'BT /F1 10 Tf 250 8 Td (NATIVE-PAGE-1) Tj ET')
footer[NameObject('/Contents')]=stream;page.merge_page(footer)
writer=PdfWriter();writer.add_page(page);path=DATA/'native-footer-scan.pdf';writer.write(path)
assert PdfReader(path).pages[0].extract_text().strip()=='NATIVE-PAGE-1'
for key in ('HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','HF_HUB_DISABLE_TELEMETRY'):os.environ[key]='1'
attempts=[]
def deny(*a,**k):attempts.append('network');raise RuntimeError('network blocked')
socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny;socket.getaddrinfo=deny
from ontology_proj.adapters.docling_parser import DoclingParser
from ontology_proj.adapters.document_normalizer import DoclingNormalizer
from ontology_proj.application.dto import ImportContext
from ontology_proj.domain.source import SourceKind,DocumentKind
parser=DoclingParser(ROOT/'data/s23-runtime/models',ocr_models_path=ROOT/'data/ocr-assets-20261013/models')
id='doc_'+hashlib.sha256(path.read_bytes()).hexdigest();result=parser.parse(str(path),id)
checks=[{'name':'parse_success','passed':result.status=='success'},{'name':'scan_detected_despite_native_footer','passed':result.ocr['pages'][0]['reader_calls']>0},{'name':'network_zero','passed':not attempts}]
if result.native_document:
 (DATA/'docling.json').write_text(json.dumps(result.native_document,ensure_ascii=False,indent=2)+'\n')
 normalized=DoclingNormalizer().convert(result.native_document,id,ImportContext(DocumentKind.INSPECTION_REPORT,SourceKind.SYNTHETIC,None),{1:0},pdf_layout=result.pdf_layout,ocr=result.ocr)
 (DATA/'normalized.json').write_text(json.dumps(normalized,ensure_ascii=False,indent=2)+'\n')
 text=' '.join(b['text'] for b in normalized['blocks']);checks.extend({'name':'token_'+token,'passed':token in text} for token in ['NATIVE-PAGE-1','桥梁','0.04','未见明显病害'])
 checks.append({'name':'all_text_source_positions','passed':all(b['locations'] for b in normalized['blocks'] if b['text'].strip())})
receipt={'status':result.status,'diagnostics':[d.code for d in result.diagnostics],'ocr':result.ocr,'checks':checks,'passed':sum(c['passed'] for c in checks),'failed':sum(not c['passed'] for c in checks),'network_attempts':attempts,'data_root':str(DATA)}
(OUT/'mixed-region.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n');print(json.dumps(receipt,ensure_ascii=False));raise SystemExit(bool(receipt['failed']))
