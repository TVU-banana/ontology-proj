import copy
import json
from pathlib import Path

import pytest

from ontology_proj.adapters.document_normalizer import DoclingNormalizer, NormalizationError
from ontology_proj.application.dto import ImportContext
from ontology_proj.domain.source import DocumentKind, SourceKind


ID = 'doc_' + 'a'*64
CONTEXT = ImportContext(DocumentKind.INSPECTION_REPORT, SourceKind.SYNTHETIC, 'B-DEMO-001')


@pytest.fixture
def native():
    return {'pages': {'1': {'page_no': 1, 'size': {'width': 100, 'height': 200}}},
            'body': {'children': [{'$ref': '#/texts/0'}]},
            'texts': [{'self_ref': '#/texts/0', 'parent': {'$ref': '#/body'}, 'text': '未见明显病害\n约 0.04 m²',
                       'label': 'text', 'content_layer': 'body', 'children': [],
                       'prov': [{'page_no': 1, 'bbox': {'l': 10, 't': 180, 'r': 70, 'b': 160, 'coord_origin': 'BOTTOMLEFT'},
                                 'charspan': [0, 6]}]}]}


def test_text_identity_order_provenance_and_input_immutability(native):
    before = copy.deepcopy(native)
    result = DoclingNormalizer().convert(native, ID, CONTEXT, {1: 0})
    block = result['blocks'][0]
    assert result['schema_version'] == 'normalized-0.1'
    assert result['source_kind'] == 'synthetic'
    assert block['block_id'] == ID + ':block:000001'
    assert block['text'] == native['texts'][0]['text']
    assert block['locations'][0]['bbox'] == [0.1, 0.1, 0.7, 0.2]
    assert block['locations'][0]['charspan'] == [0, 6]
    assert block['text'][0:6] == '未见明显病害'
    assert native == before
    result['blocks'][0]['locations'][0]['raw_bbox']['l'] = 99
    assert native == before


@pytest.mark.parametrize('case,reason', [
    ('rotation', 'PAGE_LOCATION_UNSUPPORTED'), ('unknown_rotation', 'PAGE_LOCATION_UNSUPPORTED'),
    ('origin', 'UNKNOWN_COORD_ORIGIN'), ('span', 'INVALID_CHARSPAN'),
    ('bounds', 'INVALID_BBOX'), ('degenerate', 'INVALID_BBOX'), ('page', 'PAGE_NOT_FOUND'),
    ('missing_bbox', 'INVALID_BBOX'), ('missing_prov', 'PROVENANCE_MISSING'),
])
def test_unreliable_locations_never_fabricated(native, case, reason):
    rotations = {1: 0}
    prov = native['texts'][0]['prov'][0]
    if case == 'rotation': rotations[1] = 90
    if case == 'unknown_rotation': rotations = {}
    if case == 'origin': prov['bbox']['coord_origin'] = 'UNKNOWN'
    if case == 'span': prov['charspan'] = [0, 999]
    if case == 'bounds': prov['bbox']['l'] = -1
    if case == 'degenerate': prov['bbox']['r'] = prov['bbox']['l']
    if case == 'page': prov['page_no'] = 2
    if case == 'missing_bbox': prov.pop('bbox')
    if case == 'missing_prov': native['texts'][0]['prov'] = []
    result = DoclingNormalizer().convert(native, ID, CONTEXT, rotations)
    assert result['blocks'][0]['locations'] == []
    assert result['blocks'][0]['location_status'] == 'unavailable'
    assert any(w.get('reason') == reason for w in result['warnings'])
    assert result['blocks'][0]['text'] == native['texts'][0]['text']


def test_top_left_and_multiple_spans_keep_valid_partial_locations(native):
    p = native['texts'][0]['prov'][0]
    p['bbox'].update(t=20, b=40, coord_origin='TOPLEFT')
    native['texts'][0]['prov'].append({**p, 'charspan': [0, 999]})
    block = DoclingNormalizer().convert(native, ID, CONTEXT, {1: 0})['blocks'][0]
    assert len(block['locations']) == 1
    assert block['locations'][0]['bbox'] == [0.1, 0.1, 0.7, 0.2]
    assert block['location_status'] == 'unavailable'


@pytest.mark.parametrize('case', ['cycle', 'dangling', 'size', 'id', 'nan', 'sdk'])
def test_invalid_structure_fails_safely(native, case):
    identity = ID
    if case == 'cycle': native['texts'][0]['children'] = [{'$ref': '#/body'}]
    if case == 'dangling': native['body']['children'][0]['$ref'] = '#/texts/99'
    if case == 'size': native['pages']['1']['size']['width'] = 0
    if case == 'id': identity = 'private-invalid-id'
    if case == 'nan': native['pages']['1']['size']['width'] = float('nan')
    if case == 'sdk': native['sdk'] = object()
    with pytest.raises(NormalizationError) as error:
        DoclingNormalizer().convert(native, identity, CONTEXT, {1: 0})
    assert error.value.code == 'INVALID_NATIVE_DOCUMENT'
    assert 'private' not in str(error.value)


def test_unknown_and_unreferenced_text_retained(native):
    native['pictures'] = [{'self_ref': '#/pictures/0', 'text': '图中文字', 'children': [], 'prov': []}]
    native['texts'].append({**copy.deepcopy(native['texts'][0]), 'self_ref': '#/texts/1', 'text': '原始额外文字', 'prov': []})
    native['body']['children'].append({'$ref': '#/pictures/0'})
    result = DoclingNormalizer().convert(native, ID, CONTEXT, {1: 0})
    assert [b['text'] for b in result['blocks']] == [native['texts'][0]['text'], '图中文字', '原始额外文字']
    assert any(w['code'] == 'UNREFERENCED_CONTENT' for w in result['warnings'])
    assert result['structure']['native_picture_regions'] == native['pictures']
    assert result['structure']['diagnostics'] == [{'code': 'PDF_LAYOUT_UNAVAILABLE'}]


ROOT = Path(__file__).resolve().parents[2]
VARIANTS = ['missing-area', 'additional-negative', 'duplicate-row', 'reordered-columns']


@pytest.mark.parametrize('name', VARIANTS)
def test_saved_variants_regress_exact_text_table_structure_and_positions(name):
    directory = ROOT / 'experiments/docling-variants-001/results' / name
    native = json.loads((directory / 'docling.json').read_text())
    previous = json.loads((directory / 'normalized.json').read_text())
    run = json.loads((directory / 'run.json').read_text())
    result = DoclingNormalizer().convert(native, 'doc_' + run['input_sha256'], CONTEXT,
                                        {p['page_no']: p['rotation'] for p in previous['pages']})
    current_by_ref = {b['source_ref']: b for b in result['blocks']}
    assert len(current_by_ref) == len(previous['blocks'])
    assert len(result['tables']) == len(native['tables']) == 4
    assert [b['source_ref'] for b in result['blocks']] == [b['source_ref'] for b in previous['blocks']]
    for old in previous['blocks']:
        new = current_by_ref[old['source_ref']]
        assert new['text'] == old['text']
        assert new['locations'] == old['locations']
        assert new['location_status'] == old['location_status']
        if old['kind'] == 'table_cell':
            for key in ('row', 'column', 'row_span', 'column_span', 'column_header', 'row_header'):
                assert new[key] == old[key]
    for i, table in enumerate(result['tables']):
        assert table['native_data'] == native['tables'][i]['data']
    assert [b['block_id'] for b in result['blocks']] == [f"doc_{run['input_sha256']}:block:{i:06d}" for i in range(1, len(result['blocks'])+1)]


def test_multi_page_table_retains_cells_without_guessing_locations():
    native = json.loads((ROOT / 'experiments/docling-variants-001/results/missing-area/docling.json').read_text())
    table = native['tables'][0]
    table['prov'].append({**table['prov'][0], 'page_no': 2})
    result = DoclingNormalizer().convert(native, ID, CONTEXT, {1: 0, 2: 0})
    cells = [b for b in result['blocks'] if b.get('table_id') == '#/tables/0']
    assert cells and all(b['locations'] == [] for b in cells)
    assert any(w['code'] == 'TABLE_PAGE_AMBIGUOUS' for w in result['warnings'])
    assert result['tables'][0]['native_data'] == table['data']


def test_unreferenced_future_content_type_is_reported(native):
    native['future_items'] = [{'self_ref': '#/future_items/0', 'text': '新类型原文', 'children': [], 'prov': []}]
    result = DoclingNormalizer().convert(native, ID, CONTEXT, {1: 0})
    assert result['blocks'][-1]['text'] == '新类型原文'
    assert any(w['code'] == 'UNSUPPORTED_CONTENT_TYPE' and w['content_type'] == 'future_items' for w in result['warnings'])


def test_inconsistent_table_offsets_are_rejected():
    native = json.loads((ROOT / 'experiments/docling-variants-001/results/missing-area/docling.json').read_text())
    native['tables'][0]['data']['table_cells'][0]['end_row_offset_idx'] = 99
    with pytest.raises(NormalizationError):
        DoclingNormalizer().convert(native, ID, CONTEXT, {1: 0, 2: 0})
