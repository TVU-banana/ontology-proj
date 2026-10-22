from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, NumberObject, DecodedStreamObject

from ontology_proj.adapters.pdf_layout import read_pdf_layout


def make_pdf(path, *, rotation=0):
    writer=PdfWriter();page=writer.add_blank_page(100,200)
    image=DecodedStreamObject();image.set_data(bytes([255,0,0]))
    image.update({NameObject('/Type'):NameObject('/XObject'),NameObject('/Subtype'):NameObject('/Image'),
        NameObject('/Width'):NumberObject(1),NameObject('/Height'):NumberObject(1),
        NameObject('/ColorSpace'):NameObject('/DeviceRGB'),NameObject('/BitsPerComponent'):NumberObject(8)})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/XObject'):DictionaryObject({NameObject('/Im1'):writer._add_object(image)})})
    stream=DecodedStreamObject()
    # Same underlying image appears twice. Unstroked path must not become a ruling.
    stream.set_data(b'q 30 0 0 40 10 120 cm /Im1 Do Q q 30 0 0 40 50 120 cm /Im1 Do Q 10 100 m 90 100 l S 10 90 m 90 90 l n')
    page[NameObject('/Contents')]=writer._add_object(stream)
    if rotation:page.rotate(rotation)
    writer.write(path)


def test_actual_pdf_occurrences_and_stroked_lines(tmp_path):
    p=tmp_path/'images.pdf';make_pdf(p)
    r=read_pdf_layout(p)
    assert len(r['images'])==2
    assert len({i['source_ref'] for i in r['images']})==2
    assert r['images'][0]['bbox']==[.1,.2,.4,.4]
    assert r['images'][1]['bbox']==[.5,.2,.8,.4]
    assert r['images'][0]['pixel_size']==[1,1]
    assert r['lines']==[{'page_no':1,'points':[.1,.5,.9,.5]}]
    assert not r['warnings']


def test_rotated_pdf_never_claims_supported_geometry(tmp_path):
    p=tmp_path/'images.pdf';make_pdf(p,rotation=90)
    r=read_pdf_layout(p)
    assert not r['images'] and not r['lines']
    assert r['warnings']==[{'code':'PDF_GEOMETRY_UNSUPPORTED','page_no':1}]
