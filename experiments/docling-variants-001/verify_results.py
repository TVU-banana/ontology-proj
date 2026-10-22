"""Independently verify completed conversions against frozen expected deltas.

Never patches native results or raw block text with expected facts.
"""
import copy
import hashlib
import json
import re
from pathlib import Path
from PIL import Image, ImageDraw
from normalize import normalize

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SAMPLES = ROOT / 'samples/synthetic/bridge-variants-001'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compact(text):
    return re.sub(r'\s+', '', text)


def semantic_options(value):
    # Docling serializes this capability set in nondeterministic order.
    # Do not ignore ordering of any other list or alter saved run options.
    if isinstance(value, dict):
        return {k: sorted(v) if k == 'supported_engines' and isinstance(v, list) else semantic_options(v) for k, v in value.items()}
    if isinstance(value, list):
        return [semantic_options(v) for v in value]
    return value


def matrices(doc):
    result = []
    for t in doc['tables']:
        cells = [b for b in doc['blocks'] if b.get('table_id') == t['source_ref']]
        result.append([[next((compact(b['text']) for b in cells if b['row'] == r and b['column'] == c), None) for c in range(t['num_cols'])] for r in range(t['num_rows'])])
    return result


def resolve(doc, ref):
    obj = doc
    for part in ref.removeprefix('#/').split('/'):
        obj = obj[int(part)] if isinstance(obj, list) else obj[part]
    return obj


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    expected = json.loads((SAMPLES / 'expectations.json').read_text())
    freeze = json.loads((SAMPLES / 'freeze.json').read_text())
    authored = json.loads((SAMPLES / 'validation.json').read_text())
    baseline = json.loads((ROOT / 'experiments/docling-parse-001/results/normalized.json').read_text())
    baseline_run = json.loads((ROOT / 'experiments/docling-parse-001/results/run.json').read_text())
    all_ids = set(b['block_id'] for b in baseline['blocks'])
    summaries = []
    for case in expected['cases'][1:]:
        out = HERE / 'results' / case['case_id']
        doc = normalize(out)
        run = json.loads((out / 'run.json').read_text())
        raw = json.loads((out / 'docling.json').read_text())
        checks, quotes = [], []

        def check(name, passed, detail=None):
            checks.append({'name': name, 'passed': bool(passed), 'detail': detail})

        def match(name, quote, page_no):
            hits = [b for b in doc['blocks'] if b['kind'] != 'table_cell' and quote in b['text'] and any(l['page_no'] == page_no for l in b['locations'])]
            check(name, len(hits) == 1, [b['source_ref'] for b in hits])
            if len(hits) == 1:
                b = hits[0]
                start = b['text'].index(quote)
                loc = next(l for l in b['locations'] if l['page_no'] == page_no)
                quotes.append({'check': name, 'block_id': b['block_id'], 'source_ref': b['source_ref'], 'quote': quote, 'charspan': [start, start + len(quote)], 'page_no': page_no, 'bbox': loc['bbox'], 'location_level': 'region'})
            return doc['blocks'].index(hits[0]) if len(hits) == 1 else -1

        check('conversion_success', run['status'] == 'ConversionStatus.SUCCESS' and not run['errors'])
        check('page_count_and_numbers', len(doc['pages']) == 2 and [p['page_no'] for p in doc['pages']] == [1, 2])
        check('same_versions_and_pipeline_as_baseline', run['versions'] == baseline_run['versions'] and semantic_options(run['pipeline_options']) == semantic_options(baseline_run['pipeline_options']), {'comparison': 'Only supported_engines capability-set ordering is canonicalized; raw options saved unchanged', 'raw_serialization_equal': run['pipeline_options'] == baseline_run['pipeline_options']})
        pdf = ROOT / run['input']
        artifact = next(a for a in authored['pdfs'] if a['case_id'] == case['case_id'])
        check('input_matches_authored_manifest', sha(pdf) == artifact['sha256'] == run['input_sha256'] == doc['document_id'])
        check('frozen_expected_lists_unchanged', all(sha(SAMPLES / f) == digest for f, digest in freeze['artifacts_sha256'].items()))
        check('original_pdf_and_golden_unchanged', sha(ROOT / expected['cases'][0]['planned_pdf']) == expected['derived_from']['baseline_pdf_sha256'] and sha(ROOT / expected['derived_from']['golden_path']) == expected['derived_from']['golden_sha256'])
        wanted = copy.deepcopy(matrices(baseline))
        body = [compact(b['text']) for b in baseline['blocks'] if b['kind'] != 'table_cell']
        for delta in case['changes']:
            op = delta['operation']
            if op == 'replace_paragraph':
                old, new = compact(delta['old_text']), compact(delta['new_text'])
                body[body.index(old)] = new
            elif op == 'insert_paragraph':
                body.insert(body.index(compact(delta['after_text'])) + 1, compact(delta['new_text']))
            elif op == 'replace_table_cell':
                row = next(r for r in wanted[3][1:] if delta['row_identity']['observation_code'] in r)
                row[wanted[3][0].index(compact(delta['column_header']))] = compact(delta['new_text'])
            elif op == 'duplicate_table_row_once':
                wanted[2].append([compact(c) for c in delta['row_cells']])
            elif op == 'reorder_columns':
                wanted[3] = [[compact(c) for c in delta['new_column_headers']]] + [[compact(c) for c in r] for r in delta['new_rows']]
            else:
                raise ValueError(op)
        actual = matrices(doc)
        check('four_tables_shapes_and_pages', [(t['num_rows'], t['num_cols'], t['page_no']) for t in doc['tables']] == [(len(w), len(w[0]), 1 if i < 3 else 2) for i, w in enumerate(wanted)])
        for i, (a, w) in enumerate(zip(actual, wanted), 1):
            check(f'table_{i}_all_cells_and_column_order', a == w, {'actual': a, 'expected': w})
        check('all_non_table_text_preserved_with_only_declared_changes', [compact(b['text']) for b in doc['blocks'] if b['kind'] != 'table_cell'] == body)
        anchors = []
        for fact in case['expected']['positive_facts']:
            anchors.append(match('positive_' + fact['observation_code'], fact['description_text'], fact['expected_page_no']))
        for i, statement in enumerate(case['expected']['negative_statements'], 1):
            match(f'negative_{i}', statement['text'], statement['expected_page_no'])
        for quote, page_no in [('所有观测日期均未知。报告编制日期不作为观测日期', 1), ('表中两条观测的日期均未知。', 2), ('本节观测对象为主梁，构件编号 C-GIRDER-01。', 2), ('本文件为 synthetic（合成）样本', 1)]:
            match('context_' + quote, quote, page_no)
        indexes = {t['source_ref']: next(i for i, b in enumerate(doc['blocks']) if b.get('table_id') == t['source_ref']) for t in doc['tables']}
        key_order = [indexes['#/tables/0'], indexes['#/tables/1'], anchors[0], indexes['#/tables/2'], anchors[1], anchors[2], indexes['#/tables/3']]
        check('key_reading_order', -1 not in key_order and key_order == sorted(key_order), key_order)
        if case['case_id'] == 'additional-negative':
            middle = next(i for i, b in enumerate(doc['blocks']) if b['text'] == '第三跨主梁中部未见露筋。')
            check('new_negative_between_positive_and_table', anchors[2] < middle < indexes['#/tables/3'])
        if case['case_id'] == 'duplicate-row':
            rows = [b for b in doc['blocks'] if b.get('table_id') == '#/tables/2' and b['text'] == 'D-001']
            check('duplicate_mentions_have_distinct_source_and_bbox', len(rows) == 2 and rows[0]['source_ref'] != rows[1]['source_ref'] and rows[0]['locations'][0]['bbox'] != rows[1]['locations'][0]['bbox'])
        for forbidden in case.get('must_not_retain_document_text', []):
            check('forbidden_' + forbidden + '_absent', all(forbidden not in b['text'] for b in doc['blocks']) and forbidden not in (out / 'docling.md').read_text())
        location_valid = True
        for b in doc['blocks']:
            if not b['locations'] or b['location_status'] != 'available':
                location_valid = False
            for loc in b['locations']:
                x0, y0, x1, y1 = loc['bbox']
                start, end = loc['charspan']
                location_valid &= 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1 and 0 <= start < end <= len(b['text']) and loc['page_no'] in (1, 2)
        check('all_blocks_have_valid_locations_and_spans', location_valid, len(doc['blocks']))
        check('raw_text_and_source_refs_preserved', all(resolve(raw, b['source_ref'])['text'] == b['text'] for b in doc['blocks']))
        check('sample_quote_slices_match_raw_block', all(next(b for b in doc['blocks'] if b['block_id'] == e['block_id'])['text'][slice(*e['charspan'])] == e['quote'] for e in quotes))
        ids = set(b['block_id'] for b in doc['blocks'])
        check('block_ids_unique_and_document_scoped', len(ids) == len(doc['blocks']) and ids.isdisjoint(all_ids))
        all_ids.update(ids)
        for table in doc['tables']:
            cells = [b for b in doc['blocks'] if b.get('table_id') == table['source_ref']]
            check(table['source_ref'] + '_full_grid_and_single_spans', len(cells) == table['num_rows'] * table['num_cols'] and all(b['row_span'] == b['column_span'] == 1 for b in cells))
        joined = [b['source_ref'] for b in doc['blocks'] if b['kind'] != 'table_cell' and re.search(r'[\u4e00-\u9fff] [\u4e00-\u9fff]', b['text'])]
        notes = doc['warnings'] + ([{'code': 'LINE_JOIN_SPACES', 'source_refs': joined, 'message': 'Raw wrap spaces preserved; no offsets applied to cleaned text'}] if joined else [])
        revisions = {}
        for repo, ref in [('docling-project/docling-layout-heron', 'main'), ('docling-project/docling-models', 'v2.3.0')]:
            p = Path('/path/to/user/.cache/huggingface/hub') / ('models--' + repo.replace('/', '--')) / 'refs' / ref
            revisions[repo] = {'reference': ref, 'revision': p.read_text().strip() if p.exists() else None}
        summary = {'case_id': case['case_id'], 'checks': checks, 'passed': sum(c['passed'] for c in checks), 'failed': sum(not c['passed'] for c in checks), 'blocks': len(doc['blocks']), 'tables': len(doc['tables']), 'elapsed_seconds': run['elapsed_seconds'], 'warnings': notes, 'model_cache_revisions': revisions, 'visual_review': 'pending', 'scope': 'Docling structure/text/provenance only; no business semantic classification or model calls'}
        save(out / 'verification.json', summary)
        save(out / 'evidence-checks.json', quotes)
        for p in doc['pages']:
            n = p['page_no']
            img = Image.open(out / f'page-{n}.png').convert('RGB')
            draw = ImageDraw.Draw(img)
            for b in doc['blocks']:
                for loc in b['locations']:
                    if loc['page_no'] == n:
                        x0, y0, x1, y1 = loc['bbox']
                        draw.rectangle((x0 * img.width, y0 * img.height, x1 * img.width, y1 * img.height), outline='#2464be' if b['kind'] == 'table_cell' else '#b02828', width=2)
            img.save(out / f'positions-page-{n}.png')
        summaries.append(summary)
        print(json.dumps({k: summary[k] for k in ('case_id', 'passed', 'failed', 'blocks', 'elapsed_seconds')}, ensure_ascii=False))
    save(HERE / 'results/verification.json', {'cases': summaries, 'passed': sum(s['passed'] for s in summaries), 'failed': sum(s['failed'] for s in summaries), 'model_calls': 0, 'visual_review': 'pending'})
    if any(s['failed'] for s in summaries):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
