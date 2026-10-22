"""Offline fail-closed preflight for one frozen S5.2 PDF/request pair.

Not a general parser repair, network runner, or application-wide quality gate.
Never reads golden values and never emits replacement facts or a request.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
NORMALIZED_SHA = 'c6ecc9303c1d95092480c851cb155fb6b7f919dc5ab70eda41aa9c76b1740285'
REQUEST_SHA = 'c6ba1ecf5fbb2ace8e17cc6409ffaba9da9d98889da6420626cc836c1d7efda9'


def sha(value):
    return hashlib.sha256(value).hexdigest()


def inspect(document, request):
    table = next(t for t in document['tables'] if t['table_id'] == '#/tables/13')
    cells = table['native_data']['table_cells']
    labels = {c['text']: c for c in cells if c['start_col_offset_idx'] == 0}
    owners = [labels[name] for name in ('3-6#板', '3-7#板')]
    rows = [c['start_row_offset_idx'] for c in owners]
    present = [[c for c in cells if c['start_row_offset_idx'] <= row < c['end_row_offset_idx']
                and c['start_col_offset_idx'] <= 4 < c['end_col_offset_idx']] for row in rows]
    assert not present[0] and len(present[1]) == 1
    bad = present[1][0]
    assert bad['row_span'] == bad['col_span'] == 1
    assert re.fullmatch(r'\d+\.\d+\s+\d+\.\d+', bad['text'])
    box = bad['bbox']
    assert box['coord_origin'] == 'TOPLEFT'
    for owner in owners:
        midpoint = (owner['bbox']['t'] + owner['bbox']['b']) / 2
        assert box['t'] <= midpoint <= box['b']
    ref = f"#/tables/13/data/table_cells/{cells.index(bad)}"
    block = next(b for b in document['blocks'] if b['source_ref'] == ref)
    payload = json.loads(request['messages'][1]['content'])
    assert next(t for t in payload['tables'] if t['table_id'] == table['table_id']) == table
    sent = next(b for b in payload['blocks_in_document_order'] if b['block_id'] == block['block_id'])
    assert sent['text'] == bad['text'] == block['text']
    # Original text is optional: table cells own their text in this snapshot.
    if 'original_text' in block:
        assert sent.get('original_text') == block['original_text']
    assert all(c['column_header'] for c in cells if c['start_row_offset_idx'] == 0)
    assert re.fullmatch(r'\d+-\d+#板', labels['1-34#板']['text'])
    previous = next(t for t in document['tables'] if t['table_id'] == '#/tables/12')
    assert (previous['num_cols'], table['num_cols']) == (8, 7)
    assert not any(c['column_header'] for c in previous['native_data']['table_cells'])
    continuation = next(b for b in document['blocks'] if b['source_ref'] == '#/texts/246')
    assert continuation['kind'] == 'paragraph' and not continuation.get('table_id')
    assert '8-4#' in continuation['text'] and continuation['locations'][0]['page_no'] == 19
    assert next(t for t in document['tables'] if t['table_id'] == '#/tables/9')['page_no'] == 18
    return {
        'numeric_fields': [{'row_label': name, 'row': row, 'column': 4,
                            'status': 'unusable', 'replacement_value': None}
                           for name, row in zip(('3-6#板', '3-7#板'), rows)],
        'bad_cell_source_ref': ref, 'bad_block_id': block['block_id'],
        'bad_text_repeated_in_request': ['tables/native_data/table_cells',
                                         'blocks_in_document_order/text'],
        'bbox_overlaps_two_rows_but_declares_one': True,
        'structural_boundaries': [
            {'pages': [34, 35], 'tables': ['#/tables/12', '#/tables/13'],
             'status': 'untrusted_header_and_cross_page_column_mapping'},
            {'pages': [18, 19], 'table': '#/tables/9', 'continuation_source_ref': '#/texts/246',
             'status': 'continuation_relation_not_established'},
        ],
        'raw_text_status': 'preserved_not_equivalent_to_trusted_structured_fact',
    }


def evaluate(normalized_bytes, request_bytes):
    result = {'status': 'blocked', 'eligible_for_send': False, 'request_emitted': False,
              'authorization': 'model_outbound_prohibited', 'actual_parser_repair': False,
              'scope': 'specific_frozen_pair_not_general_application_gate'}
    if sha(normalized_bytes) != NORMALIZED_SHA or sha(request_bytes) != REQUEST_SHA:
        return {**result, 'reason': 'INPUT_CHANGED_REVIEW_AND_NEW_FREEZE_REQUIRED'}
    try:
        findings = inspect(json.loads(normalized_bytes), json.loads(request_bytes))
    except (AssertionError, KeyError, StopIteration, ValueError, TypeError):
        return {**result, 'reason': 'INSPECTION_FAILED_CLOSED'}
    return {**result, 'reason': 'KNOWN_TABLE_DEFECTS_UNRESOLVED', 'findings': findings}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--normalized', type=Path, required=True)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--result', type=Path, required=True)
    args = parser.parse_args()
    # Refuse overwriting completed results, even for an input read failure.
    with args.result.open('x') as stream:
        try:
            result = evaluate(args.normalized.read_bytes(), args.request.read_bytes())
        except OSError:
            result = {'status': 'blocked', 'eligible_for_send': False,
                      'request_emitted': False, 'reason': 'INPUT_UNAVAILABLE'}
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'reason': result['reason']}))
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
