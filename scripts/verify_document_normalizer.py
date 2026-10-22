"""T03 offline snapshot regression, with PDF geometry readback; no conversions."""
import argparse
import hashlib
import json
from pathlib import Path

import pypdfium2 as pdfium

from ontology_proj.application.dto import ImportContext
from ontology_proj.bootstrap import build_normalizer
from ontology_proj.domain.source import DocumentKind, SourceKind


VARIANTS = ('missing-area', 'additional-negative', 'duplicate-row', 'reordered-columns')


def resolve(native, ref):
    value = native
    for token in ref[2:].split('/'):
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('--output-dir', type=Path, required=True)
    args = cli.parse_args()
    root = Path(__file__).resolve().parents[1]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for name in VARIANTS:
        directory = root / 'experiments/docling-variants-001/results' / name
        native_bytes = (directory / 'docling.json').read_bytes()
        native = json.loads(native_bytes)
        run = json.loads((directory / 'run.json').read_text())
        old = json.loads((directory / 'normalized.json').read_text())
        pdf_path = root / run['input']
        digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        rotations, geometry = {}, {}
        with pdfium.PdfDocument(pdf_path) as pdf:
            for i in range(len(pdf)):
                page = pdf[i]
                rotations[i+1] = page.get_rotation()
                geometry[i+1] = page.get_size()
                page.close()
        output = build_normalizer().convert(native, 'doc_' + digest,
                    ImportContext(DocumentKind.INSPECTION_REPORT, SourceKind.SYNTHETIC, 'B-DEMO-001'), rotations)
        new_blocks = output['blocks']
        output_path = args.output_dir / (name + '-normalized.json')
        output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
        checks = {
            'source_hash_matches_saved_run': digest == run['input_sha256'],
            'native_input_unchanged': (directory / 'docling.json').read_bytes() == native_bytes,
            'explicit_document_structure_version': output['schema_version'] == 'normalized-0.1',
            'unique_contiguous_block_ids': [b['block_id'] for b in new_blocks] == [f'doc_{digest}:block:{i:06d}' for i in range(1, len(new_blocks)+1)],
            'source_order_preserved': [b['source_ref'] for b in new_blocks] == [b['source_ref'] for b in old['blocks']],
            'raw_text_preserved': all(b['text'] == resolve(native, b['source_ref'])['text'] for b in new_blocks),
            'saved_locations_preserved': [b['locations'] for b in new_blocks] == [b['locations'] for b in old['blocks']],
            'character_spans_valid': all(0 <= loc['charspan'][0] <= loc['charspan'][1] <= len(b['text']) for b in new_blocks for loc in b['locations']),
            'bbox_and_page_valid': all(loc['page_no'] in rotations and len(loc['bbox']) == 4 and all(0 <= x <= 1 for x in loc['bbox']) and loc['bbox'][0] < loc['bbox'][2] and loc['bbox'][1] < loc['bbox'][3] for b in new_blocks for loc in b['locations']),
            'page_geometry_matches_pdf': len(output['pages']) == len(geometry) and all(abs(p['width']-geometry[p['page_no']][0]) < 0.01 and abs(p['height']-geometry[p['page_no']][1]) < 0.01 and p['rotation'] == rotations[p['page_no']] for p in output['pages']),
            'complete_tables_preserved': len(output['tables']) == 4 and [t['native_data'] for t in output['tables']] == [t['data'] for t in native['tables']],
            'source_classification_preserved': output['source_kind'] == 'synthetic',
            'no_business_entities_generated': 'entities' not in output and 'records' not in output,
        }
        summaries.append({'variant': name, 'checks': checks, 'passed': all(checks.values()),
                          'blocks': len(new_blocks), 'tables': len(output['tables']), 'pages': len(output['pages']),
                          'located_blocks': sum(bool(b['locations']) for b in new_blocks),
                          'warnings': output['warnings'], 'native_sha256': hashlib.sha256(native_bytes).hexdigest(),
                          'derived_output': output_path.name, 'derived_sha256': hashlib.sha256(output_path.read_bytes()).hexdigest()})
    result = {'task': 'S2.1-T03', 'passed': all(r['passed'] for r in summaries), 'variants': summaries,
              'checks': sum(len(r['checks']) for r in summaries), 'blocks': sum(r['blocks'] for r in summaries),
              'boundary': 'Offline saved Docling snapshots; PDF read only for page geometry/rotation. No Docling conversion, model API, Neo4j or new visual acceptance. Golden facts not read.'}
    (args.output_dir / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ['task', 'passed', 'checks', 'blocks']}))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
