"""One isolated local validation; saved Docling response is explicitly a double."""
import hashlib
import json
from pathlib import Path
import re
import socket

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'specs/010-document-structure/results'
DATA = ROOT / 'data/document-structure-20261013-01'


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    DATA.mkdir(exist_ok=False)
    attempts=[]
    def deny(*a, **kw):
        attempts.append('network');raise RuntimeError('Local-only verification')
    socket.socket.connect=deny;socket.create_connection=deny;socket.getaddrinfo=deny
    from ontology_proj.adapters.pdf_layout import read_pdf_layout
    from ontology_proj.adapters.local_processing_store import LocalProcessingStore
    from ontology_proj.adapters.document_normalizer import DoclingNormalizer
    from ontology_proj.application.local_parsing import LocalParsingService
    from ontology_proj.application.parsing import ParseOutcome, ParseStatus
    from ontology_proj.application.dto import ImportContext
    from ontology_proj.domain.source import DocumentKind, SourceKind
    from PIL import ImageDraw
    import pypdfium2 as pdfium

    source=ROOT/'input-pdfs/胜利大桥-复杂病害表与照片说明-4页摘录.pdf'
    olddir=ROOT/'data/trial-complex-parse-20261013-01/parsed'
    native_path=olddir/'docling.json';old_path=olddir/'normalized.json'
    before={str(p.relative_to(ROOT)):digest(p) for p in [source,native_path,old_path]}
    freeze=json.loads((ROOT/'specs/008-system-acceptance/results/trial-complex-input-20261013-01/parser-result.json').read_text())
    assert digest(native_path)==freeze['native_sha256'] and digest(old_path)==freeze['normalized_sha256']
    native=json.loads(native_path.read_text());old=json.loads(old_path.read_text())

    class SavedDoclingResponse:
        calls=0
        def configuration(self):return {'verification_double':'saved-real-docling-response','native_sha256':digest(native_path),'pdf_layout_revision':'pdf-layout-1'}
        def parse(self,path,document_id):
            self.calls+=1
            # Real geometry reads the actual source, not the expected answers.
            return ParseOutcome(document_id,ParseStatus.SUCCESS,native,(),freeze['versions'],0,
                                read_pdf_layout(Path(path)))
    parser=SavedDoclingResponse();store=LocalProcessingStore(DATA/'runtime')
    service=LocalParsingService(store,parser,DoclingNormalizer())
    prepared=service.prepare(source.read_bytes(),source.name,ImportContext(DocumentKind.INSPECTION_REPORT,SourceKind.REAL,None))
    identity=prepared.source.document_id
    processed=service.process(identity);n=store.read_artifact(identity,'normalized');s=n['structure']
    checks=[]
    def check(name,passed,detail=None):checks.append({'name':name,'passed':bool(passed),'detail':detail})
    check('original blocks unchanged',n['blocks']==old['blocks'])
    check('original native table unchanged',n['tables']==old['tables'])
    check('original pages unchanged',n['pages']==old['pages'])
    check('SDK native result saved unchanged',store.read_artifact(identity,'docling')==native)
    check('20 individual photos',len(s['pictures'])==20)
    check('page image counts',[sum(p['page_no']==i for p in s['pictures']) for i in range(1,5)]==[0,6,8,6])
    ordered=sorted(s['pictures'],key=lambda p:(p['page_no'],round(p['bbox'][1],2),p['bbox'][0]))
    blocks={b['block_id']:b for b in n['blocks']};relations=[r for r in s['relations'] if r['kind']=='picture_caption']
    annotations=[]
    for number,picture in zip(range(19,39),ordered):
        match=[r for r in relations if r['picture_id']==picture['picture_id']]
        caption=blocks[match[0]['caption_block_id']] if len(match)==1 else None
        # Expected order comes from original page review frozen before implementation.
        good=caption is not None and re.match(r'\s*图\s*6\.1-'+str(number)+r'(?!\d)',caption['text'])
        check('photo-caption figure '+str(number),good,{'picture':picture,'relation':match,'caption':caption['text'] if caption else None})
        annotations.append((number,picture,caption))
    check('2 cross-page photo links',sum(r['method']=='adjacent_page_ruled_column_edges' for r in relations)==2)
    fragments=[r for r in s['relations'] if r['kind']=='table_cell_continuation']
    check('continuation targets last row description',len(fragments)==1 and (fragments[0]['row'],fragments[0]['column'])==(8,1),fragments)
    check('continuation raw text retained',len(fragments)==1 and '8-4#' in blocks[fragments[0]['continuation_block_id']]['text'])
    check('no unresolved structure',not s['diagnostics'],s['diagnostics'])
    manifest=service.read(identity)
    check('published normalized hash matches',manifest['artifacts']['normalized.json']['sha256']==digest(store._directory(identity)/'normalized.json'))
    check('duplicate process reuses snapshot',service.process(identity)['reused'] and parser.calls==1)
    check('no source/snapshot changes',all(digest(ROOT/key)==value for key,value in before.items()))
    check('no network attempts',not attempts)
    overlays=DATA/'overlays';overlays.mkdir()
    with pdfium.PdfDocument(str(source)) as pdf:
        for i in range(1,5):
            page=pdf[i-1]
            try:rendered=page.render(scale=1.7).to_pil().convert('RGB')
            finally:page.close()
            draw=ImageDraw.Draw(rendered);w,h=rendered.size
            for number,pic,caption in annotations:
                if pic['page_no']==i:
                    b=pic['bbox'];draw.rectangle([b[0]*w,b[1]*h,b[2]*w,b[3]*h],outline='blue',width=3)
                    draw.text((b[0]*w+4,b[1]*h+4),f'Fig 6.1-{number}',fill='blue')
                if caption and caption['locations'][0]['page_no']==i:
                    b=caption['locations'][0]['bbox'];draw.rectangle([b[0]*w,b[1]*h,b[2]*w,b[3]*h],outline='red',width=2)
            rendered.save(overlays/f'page-{i}.png')
    write(OUT/'real-input-checks.json',{'passed':sum(c['passed'] for c in checks),'failed':sum(not c['passed'] for c in checks),'checks':checks,
        'service_operation':processed,'manifest':manifest,'baseline_hashes':before,
        'evidence_kind':'actual PDF geometry + saved real Docling snapshot normalization; service uses explicit SDK response double',
        'new_docling_conversions':0,'generation_calls':0,'embedding_calls':0,'graph_writes':0,'network_attempts':attempts,
        'normalized_key':str((store._directory(identity)/'normalized.json').relative_to(ROOT))})
    print(json.dumps({'passed':sum(c['passed'] for c in checks),'failed':sum(not c['passed'] for c in checks),'images':len(s['pictures']),'relations':len(s['relations'])}))
    return 0 if all(c['passed'] for c in checks) else 1


if __name__=='__main__':raise SystemExit(main())
