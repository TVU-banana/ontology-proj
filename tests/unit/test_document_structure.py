"""Synthetic independent geometry cases; no Docling, providers or real graph."""
import copy

import pytest

from ontology_proj.adapters.document_structure import enrich_structure

ID = 'doc_' + 'a'*64


def block(number, text, page, box, kind='paragraph', **extra):
    return {'block_id':f'{ID}:block:{number:06d}','source_ref':f'#/texts/{number}',
            'text':text,'kind':kind,'location_status':'available','content_layer':'body',
            'locations':[{'page_no':page,'bbox':box,'charspan':[0,len(text)]}], **extra}


def image(page, box, number=1):
    return {'source_ref':f'pdf:page:{page}:object:{number}', 'page_no':page, 'bbox':box,
            'pixel_size':[500,300],'location_level':'region'}


def ruled(page, box):
    x,y,r,b=box
    return [{'page_no':page,'points':p} for p in [[x,y,r,y],[x,b,r,b],[x,y,x,b],[r,y,r,b]]]


@pytest.fixture
def case():
    document={'document_id':ID,'pages':[{'page_no':p,'width':100,'height':200,'rotation':0} for p in [1,2]],
              'blocks':[block(1,'图9.7-62 独立说明',1,[.2,.52,.4,.535])], 'tables':[]}
    layout={'revision':'pdf-layout-1','pages':copy.deepcopy(document['pages']),
            'images':[image(1,[.1,.2,.45,.5])],
            'lines':ruled(1,[.09,.19,.46,.505])+ruled(1,[.09,.505,.46,.54]),'warnings':[]}
    return document,layout


def links(document,layout):
    return enrich_structure(document,{},layout)


def test_same_page_source_backed_link_and_input_immutability(case):
    document,layout=case; before=copy.deepcopy(case)
    s=links(*case)
    assert len(s['pictures'])==len(s['relations'])==1
    assert s['relations'][0]['caption_block_id']==document['blocks'][0]['block_id']
    assert s['relations'][0]['status']=='derived'
    assert not s['diagnostics'] and case==before
    s['source_layout']['images'][0]['bbox'][0]=0
    assert case==before


@pytest.mark.parametrize('change',['caption_above','wrong_column','missing_number','duplicate_caption','duplicate_image','intervening_caption','unavailable'])
def test_same_page_rejects_wrong_or_ambiguous_binding(case,change):
    d,l=case;b=d['blocks'][0]
    if change=='caption_above':b['locations'][0]['bbox']=[.2,.1,.4,.12]
    if change=='wrong_column':b['locations'][0]['bbox']=[.6,.52,.8,.535]
    if change=='missing_number':b['text']='普通说明，没有图号'
    if change=='duplicate_caption':d['blocks'].append(block(2,b['text'],1,b['locations'][0]['bbox']))
    if change=='duplicate_image':l['images'].append(image(1,l['images'][0]['bbox'],2))
    if change=='intervening_caption':d['blocks'].append(block(2,'图4.2-11 干扰说明',1,[.2,.51,.4,.515]))
    if change=='unavailable':b['location_status']='unavailable'
    s=links(d,l)
    if change=='intervening_caption':
        assert not any(r['caption_block_id']==b['block_id'] for r in s['relations'])
    else:assert not s['relations']
    assert s['diagnostics']


def cross_case(case):
    d,l=case
    l['images'][0]['bbox']=[.1,.7,.45,.9]
    d['blocks'][0]['locations'][0].update(page_no=2,bbox=[.2,.09,.4,.105])
    l['lines']=ruled(1,[.09,.69,.46,.91])+ruled(2,[.09,.08,.46,.12])
    return d,l


def test_cross_page_uses_matching_ruled_columns(case):
    d,l=cross_case(case);s=links(d,l)
    assert len(s['relations'])==1 and s['relations'][0]['method']=='adjacent_page_ruled_column_edges'


@pytest.mark.parametrize('change',['missing_rules','width','middle_page','new_title','intervening_caption','duplicate_number'])
def test_cross_page_rejects_weak_or_conflicting_evidence(case,change):
    d,l=cross_case(case)
    if change=='missing_rules':l['lines']=[]
    if change=='width':l['lines']=ruled(1,[.09,.69,.46,.91])+ruled(2,[.1,.08,.5,.12])
    if change=='middle_page':d['blocks'][0]['locations'][0]['bbox']=[.2,.3,.4,.32]
    if change=='new_title':d['blocks'].append(block(2,'另一章节',2,[.2,.06,.4,.075],'title'))
    if change=='intervening_caption':d['blocks'].append(block(2,'图3.1-33 另一说明',1,[.2,.91,.4,.925]))
    if change=='duplicate_number':d['blocks'].append(block(2,d['blocks'][0]['text'],2,[.6,.09,.8,.105]))
    s=links(d,l)
    assert not any(r['caption_block_id']==d['blocks'][0]['block_id'] for r in s['relations'])


@pytest.fixture
def table_case(case):
    d,l=case;l['images']=[]
    d['tables']=[{'table_id':'#/tables/0','page_no':1,'num_rows':3,'num_cols':3}]
    d['blocks']=[block(1,'12#组',1,[.15,.85,.23,.87],'table_cell',table_id='#/tables/0',row=2,column=0),
        block(2,'已知原文 A=3.1',1,[.4,.85,.6,.88],'table_cell',table_id='#/tables/0',row=2,column=1),
        block(3,'12-9#条目 A=0.8',2,[.4,.09,.6,.105])]
    l['lines']=ruled(1,[.1,.82,.3,.91])+ruled(1,[.3,.82,.75,.91])+ruled(2,[.3,.08,.75,.12])
    return d,l


def test_table_fragment_links_without_changing_text_or_rows(table_case):
    before=copy.deepcopy(table_case);s=links(*table_case)
    assert len(s['relations'])==1
    r=s['relations'][0]
    assert r['kind']=='table_cell_continuation' and (r['row'],r['column'])==(2,1)
    assert r['continuation_block_id']==table_case[0]['blocks'][2]['block_id']
    assert r['target_block_id']==table_case[0]['blocks'][1]['block_id']
    assert table_case==before


@pytest.mark.parametrize('change',['different_marker','new_header','new_title','width','no_grid','nonadjacent','populated_row','ambiguous_table'])
def test_table_fragment_refuses_guessing(table_case,change):
    d,l=table_case
    if change=='different_marker':d['blocks'][2]['text']='99-1#条目 A=0.8'
    if change=='new_header':d['blocks'].append(block(4,'新表头',2,[.15,.09,.23,.105],'table_cell'))
    if change=='new_title':d['blocks'].append(block(4,'新表标题',2,[.4,.05,.6,.07],'title'))
    if change=='width':l['lines']=l['lines'][:-4]+ruled(2,[.31,.08,.77,.12])
    if change=='no_grid':l['lines']=[]
    if change=='nonadjacent':d['tables'][0]['page_no']=2
    if change=='populated_row':d['blocks'].append(block(4,'非空独立行头',2,[.15,.09,.23,.105]))
    if change=='ambiguous_table':
        d['tables'].append({**d['tables'][0],'table_id':'#/tables/1'})
        d['blocks'].extend([{**copy.deepcopy(b),'table_id':'#/tables/1','block_id':b['block_id']+'x'} for b in d['blocks'][:2]])
    assert not links(d,l)['relations']


@pytest.mark.parametrize('change',['nan','out_of_bounds','rotation','missing_page','duplicate_source','invalid_line','invalid_pixel_size'])
def test_bad_pdf_geometry_rejected(case,change):
    d,l=case
    if change=='nan':l['images'][0]['bbox'][0]=float('nan')
    if change=='out_of_bounds':l['images'][0]['bbox'][2]=2
    if change=='rotation':d['pages'][0]['rotation']=l['pages'][0]['rotation']=90
    if change=='missing_page':l['pages'].pop()
    if change=='duplicate_source':l['images'].append(copy.deepcopy(l['images'][0]))
    if change=='invalid_line':l['lines'][0]['points'][1]=-1
    if change=='invalid_pixel_size':l['images'][0]['pixel_size'][0]=0
    with pytest.raises(ValueError):links(d,l)


def test_native_explicit_caption_requires_unique_individual_image(case):
    d,l=case;b=d['blocks'][0];b['text']='无图号但SDK明确引用'
    native={'pictures':[{'self_ref':'#/pictures/0','captions':[{'$ref':b['source_ref']}],
        'prov':[{'page_no':1,'bbox':{'l':9,'t':38,'r':46,'b':102,'coord_origin':'TOPLEFT'}}]}]}
    s=enrich_structure(d,native,l)
    assert s['relations'][0]['method']=='native_explicit_unique_image'
    l['images'].append(image(1,l['images'][0]['bbox'],2))
    assert not enrich_structure(d,native,l)['relations']


def test_missing_supplement_retains_native_and_safe_diagnostic(case):
    d,l=case;native={'pictures':[{'self_ref':'#/pictures/0','prov':[],'children':[]}]}
    s=enrich_structure(d,native)
    assert s['native_picture_regions']==native['pictures']
    assert not s['pictures'] and not s['relations']
    assert s['diagnostics']==[{'code':'PDF_LAYOUT_UNAVAILABLE'}]
