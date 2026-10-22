"""Conservative source-backed structure links, separate from business facts."""
import copy
import math
import re

REVISION = 'document-structure-1'
FIGURE = re.compile(r'^\s*(?:图\s*\d+(?:[.．-]\d+)+|Figure\s+\d+(?:[.-]\d+)*)\b', re.I)
TOLERANCE = .006


def _box(value):
    return (isinstance(value, list) and len(value) == 4
            and all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in value)
            and value[0] < value[2] and value[1] < value[3])


def _contains(a, b):
    return all((a[0]-TOLERANCE <= b[0], a[1]-TOLERANCE <= b[1],
                a[2]+TOLERANCE >= b[2], a[3]+TOLERANCE >= b[3]))


def _aligned(a, b):
    return abs(a[0]-b[0]) <= TOLERANCE and abs(a[2]-b[2]) <= TOLERANCE


def _cell(page, box, lines):
    """Smallest rectangle enclosing a region, supported by four ruling lines."""
    relevant = [l['points'] for l in lines if l['page_no'] == page]
    horizontal = [l for l in relevant if abs(l[1]-l[3]) < 1e-5
                  and min(l[0],l[2]) <= box[0]+TOLERANCE and max(l[0],l[2]) >= box[2]-TOLERANCE]
    tops = [l[1] for l in horizontal if l[1] <= box[1]+TOLERANCE]
    bottoms = [l[1] for l in horizontal if l[1] >= box[3]-TOLERANCE]
    if not tops or not bottoms: return None
    top, bottom = max(tops), min(bottoms)
    vertical = [l for l in relevant if abs(l[0]-l[2]) < 1e-5
                and min(l[1],l[3]) <= top+TOLERANCE and max(l[1],l[3]) >= bottom-TOLERANCE]
    lefts = [l[0] for l in vertical if l[0] <= box[0]+TOLERANCE]
    rights = [l[0] for l in vertical if l[0] >= box[2]-TOLERANCE]
    if not lefts or not rights: return None
    cell = [max(lefts), top, min(rights), bottom]
    return cell if _box(cell) else None


def enrich_structure(document, native, layout=None):
    pages = {p['page_no']: p for p in document['pages']}
    regions = copy.deepcopy(native.get('pictures', []))
    structure = {'revision': REVISION, 'native_picture_regions': regions, 'pictures': [],
                 'relations': [], 'diagnostics': []}
    if layout is None:
        if regions: structure['diagnostics'].append({'code': 'PDF_LAYOUT_UNAVAILABLE'})
        return structure
    if layout.get('revision') == 'pdf-layout-1' and layout.get('pages') == [] and layout.get('warnings') == [{'code': 'PDF_LAYOUT_UNAVAILABLE'}]:
        structure['diagnostics'].append({'code': 'PDF_LAYOUT_UNAVAILABLE'})
        return structure
    # Reject forged/out-of-contract geometry rather than deriving links from it.
    if layout.get('revision') != 'pdf-layout-1' or not isinstance(layout.get('warnings'), list): raise ValueError
    if len(layout['pages']) != len(pages) or set(pages) != {p['page_no'] for p in layout['pages']}: raise ValueError
    for p in layout['pages']:
        q = pages[p['page_no']]
        if any(abs(p[k]-q[k]) > .1 for k in ('width','height')) or p['rotation'] != q['rotation']: raise ValueError
    sources = set()
    for image in layout['images']:
        page = image['page_no']
        if (page not in pages or pages[page]['rotation'] != 0 or not _box(image['bbox'])
                or not re.fullmatch(r'pdf:page:'+str(page)+r':object:\d+', image['source_ref'])
                or image['source_ref'] in sources): raise ValueError
        if (not isinstance(image['pixel_size'],list) or len(image['pixel_size'])!=2
                or any(type(v) is not int or v<1 for v in image['pixel_size'])): raise ValueError
        sources.add(image['source_ref'])
        structure['pictures'].append({**copy.deepcopy(image), 'picture_id': document['document_id']+':'+image['source_ref'],
                                       'origin': 'pdf_image_occurrence'})
    for line in layout['lines']:
        if line['page_no'] not in pages or pages[line['page_no']]['rotation'] != 0: raise ValueError
        pts = line['points']
        if (not isinstance(pts,list) or len(pts)!=4
                or not all(type(v) in (int,float) and math.isfinite(v) and 0 <= v <= 1 for v in pts)
                or not (abs(pts[0]-pts[2]) < 1e-5 or abs(pts[1]-pts[3]) < 1e-5)): raise ValueError
    structure['source_layout'] = copy.deepcopy(layout)
    structure['diagnostics'].extend(copy.deepcopy(layout['warnings']))
    pictures, lines = structure['pictures'], layout['lines']
    blocks = [b for b in document['blocks'] if b['location_status']=='available' and len(b['locations'])==1]
    by_ref = {b['source_ref']:b for b in blocks}
    captions = [b for b in blocks if b['kind']=='paragraph' and FIGURE.search(b['text'])]
    caption_numbers = [re.sub(r'\s+', '', FIGURE.search(b['text'])[0]) for b in captions]
    duplicate_captions = {b['block_id'] for b, number in zip(captions, caption_numbers)
                          if caption_numbers.count(number) > 1}
    proposals = []

    def propose(pic, caption, method, evidence):
        proposals.append({'kind':'picture_caption','picture_id':pic['picture_id'],
                          'caption_block_id':caption['block_id'],'status':'derived',
                          'method':method,'evidence':evidence})

    # Explicit SDK captions are usable only when the region maps to one actual image.
    for region in regions:
        for prov in region.get('prov',[]):
            raw = prov.get('bbox',{});page = prov.get('page_no');p=pages.get(page)
            if not p or raw.get('coord_origin') not in ('BOTTOMLEFT','TOPLEFT'): continue
            box=[raw['l']/p['width'],raw['t']/p['height'],raw['r']/p['width'],raw['b']/p['height']]
            if raw['coord_origin']=='BOTTOMLEFT':box[1],box[3]=1-box[1],1-box[3]
            matches=[pic for pic in pictures if pic['page_no']==page and _contains(box,pic['bbox'])]
            refs=region.get('captions',[])
            if len(matches)==1 and len(refs)==1 and refs[0].get('$ref') in by_ref:
                propose(matches[0],by_ref[refs[0]['$ref']],'native_explicit_unique_image',{'native_region':region['self_ref']})

    for caption in captions:
        loc=caption['locations'][0];page=loc['page_no'];box=loc['bbox'];cell=_cell(page,box,lines)
        # Same-page match needs horizontal coverage and no intervening picture/caption.
        candidates=[]
        for pic in pictures:
            b=pic['bbox']
            if pic['page_no']!=page or b[3]>box[1]+TOLERANCE or box[1]-b[3]>.06: continue
            if not (b[0]-TOLERANCE <= box[0] and box[2] <= b[2]+TOLERANCE): continue
            piccell=_cell(page,b,lines)
            if cell and piccell and not _aligned(cell,piccell): continue
            obstructed=any(other is not caption and other['locations'][0]['page_no']==page
                           and b[3] < other['locations'][0]['bbox'][1] < box[1]
                           and other['locations'][0]['bbox'][0] < b[2] and other['locations'][0]['bbox'][2] > b[0]
                           for other in captions)
            obstructed = obstructed or any(other is not pic and other['page_no']==page
                and b[3] <= other['bbox'][1] < box[1]
                and other['bbox'][0]<b[2] and other['bbox'][2]>b[0] for other in pictures)
            if not obstructed:candidates.append(pic)
        if candidates:
            for pic in candidates:propose(pic,caption,'same_page_below_aligned',{'caption_location':loc,'picture_bbox':pic['bbox']})
            continue
        # Cross-page links require explicit matching column borders and edge positions.
        if page <= 1 or box[3]>.15 or not cell: continue
        if any(b['locations'][0]['page_no']==page and b['kind']=='title'
               and b['locations'][0]['bbox'][3] < box[1] for b in blocks): continue
        for pic in pictures:
            b=pic['bbox'];piccell=_cell(page-1,b,lines)
            if (pic['page_no']==page-1 and b[3]>.85 and piccell and _aligned(cell,piccell)
                    and not any(c['locations'][0]['page_no']==page-1
                                and c['locations'][0]['bbox'][1]>b[3]
                                and c['locations'][0]['bbox'][0]<b[2] and c['locations'][0]['bbox'][2]>b[0]
                                for c in captions)):
                propose(pic,caption,'adjacent_page_ruled_column_edges',{'caption_location':loc,'picture_cell':piccell,'caption_cell':cell})

    # All uniqueness decisions are global, independent of traversal order.
    unique = {(r['picture_id'],r['caption_block_id']):r for r in proposals}
    proposals=list(unique.values())
    for relation in proposals:
        if (relation['caption_block_id'] not in duplicate_captions
                and sum(r['picture_id']==relation['picture_id'] for r in proposals)==1
                and sum(r['caption_block_id']==relation['caption_block_id'] for r in proposals)==1):
            structure['relations'].append(relation)
        else:
            structure['diagnostics'].append({'code':'PICTURE_CAPTION_AMBIGUOUS',
                'picture_id':relation['picture_id'],'caption_block_id':relation['caption_block_id']})
    for pic in pictures:
        if not any(r.get('picture_id')==pic['picture_id'] for r in structure['relations']):
            structure['diagnostics'].append({'code':'PICTURE_CAPTION_UNRESOLVED','picture_id':pic['picture_id']})
    for caption in captions:
        if not any(r.get('caption_block_id')==caption['block_id'] for r in structure['relations']):
            structure['diagnostics'].append({'code':'CAPTION_PICTURE_UNRESOLVED','caption_block_id':caption['block_id']})

    # Only a ruled fragment with the same numeric row marker can extend a last row.
    for block in blocks:
        if block['kind']!='paragraph' or FIGURE.search(block['text']):continue
        loc=block['locations'][0];page=loc['page_no'];box=loc['bbox']
        if page<=1 or box[3]>.15:continue
        cell=_cell(page,box,lines)
        if not cell:continue
        options=[]
        for table in document['tables']:
            if table['page_no']!=page-1:continue
            last=[b for b in blocks if b.get('table_id')==table['table_id'] and b.get('row')==table['num_rows']-1]
            identity=next((b for b in last if b.get('column')==0),None)
            if not identity:continue
            row_marker=re.match(r'\s*(\d+)\s*[#＃]',identity['text'])
            fragment_marker=re.match(r'\s*(\d+)\s*[-－]',block['text'])
            if not row_marker or not fragment_marker or row_marker[1]!=fragment_marker[1]:continue
            for target in last:
                targetloc=target['locations'][0];targetcell=_cell(page-1,targetloc['bbox'],lines)
                if target['column']==0 or targetloc['bbox'][3]<.85 or not targetcell or not _aligned(cell,targetcell):continue
                # Reject titles/new headers and any populated neighbour in the continuation row.
                firstcell=next((c for c in last if c['column']==0),None)
                firstgrid=_cell(page-1,firstcell['locations'][0]['bbox'],lines)
                if not firstgrid:continue
                rowothers=[b for b in blocks if b is not block and b['locations'][0]['page_no']==page
                           and abs(b['locations'][0]['bbox'][1]-box[1])<.02
                           and b['locations'][0]['bbox'][0]>=firstgrid[0]-TOLERANCE]
                preceding=[b for b in blocks if b['locations'][0]['page_no']==page
                           and b['locations'][0]['bbox'][3]<box[1] and b['kind']=='title']
                if rowothers or preceding:continue
                options.append({'kind':'table_cell_continuation','table_id':table['table_id'],
                    'row':target['row'],'column':target['column'],'target_block_id':target['block_id'],
                    'continuation_block_id':block['block_id'],'status':'derived',
                    'method':'adjacent_ruled_column_numeric_row_marker',
                    'evidence':{'target_cell':targetcell,'continuation_cell':cell,
                                'row_label_block_id':identity['block_id'],'fragment_location':loc}})
        if len(options)==1:structure['relations'].extend(options)
        elif options:structure['diagnostics'].append({'code':'TABLE_CONTINUATION_AMBIGUOUS','block_id':block['block_id']})
        elif re.match(r'\s*\d+\s*[-－]',block['text']):
            structure['diagnostics'].append({'code':'TABLE_CONTINUATION_UNRESOLVED','block_id':block['block_id']})
    return structure
