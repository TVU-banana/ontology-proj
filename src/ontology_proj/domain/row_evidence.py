"""Bounded evidence for ordinary, explicitly numbered component table rows.

No inferred coordinates, fuzzy identifiers, cross-page joins or source rewriting.
"""
import re


def compact(value):
    return re.sub(r'\s+', '', value or '')


class RowEvidence:
    def __init__(self, document):
        self.blocks = {b['block_id']: b for b in document['blocks']}

    def source_block(self, source):
        block = self.blocks.get(source.get('block_id'))
        quote = source.get('exact_quote')
        if block and isinstance(quote, str) and quote and quote in block['text']:
            return block
        return None

    def key(self, block):
        if (block and block.get('kind') == 'table_cell'
                and isinstance(block.get('row'), int) and block['row'] > 0
                and block.get('row_span', 1) == block.get('column_span', 1) == 1):
            return block.get('table_id'), block['row']
        return None

    def cells(self, key):
        if key is None:
            return []
        return [b for b in self.blocks.values()
                if b.get('table_id') == key[0] and b.get('row') == key[1]]

    def component_row(self, key, code):
        cells = self.cells(key)
        if not code or not cells or any(self.key(b) != key for b in cells):
            return False
        pages = {b['locations'][0]['page_no'] for b in cells if len(b.get('locations', [])) == 1}
        if len(pages) != 1 or any(len(b.get('locations', [])) != 1 for b in cells):
            return False
        # A complete explicitly labelled code cell, never substring matching.
        codes = [b for b in cells if any(
            h.get('table_id') == key[0] and h.get('row') == 0
            and h.get('column') == b.get('column')
            and compact(h['text']) in ('构件编号', '构件原编号')
            and h.get('column_span', 1) == h.get('row_span', 1) == 1
            for h in self.blocks.values())]
        return len(codes) == 1 and compact(codes[0]['text']) == compact(code)

    def field_in_row(self, sources, field, key, value=None):
        return any(s.get('target_field') == field
                   and self.key(self.source_block(s)) == key
                   and (value is None or compact(value) in compact(s['exact_quote']))
                   for s in sources)

    def observation_row(self, observation, sources, component, defect, defect_sources):
        description = observation.get('description')
        keys = {self.key(self.source_block(s)) for s in sources
                if s.get('target_field') == 'description' and description
                and description in s.get('exact_quote', '')}
        if len(keys) != 1 or None in keys:
            return None
        key = next(iter(keys))
        if not self.component_row(key, component.get('component_code')):
            return None
        if not self.field_in_row(defect_sources, 'location_text', key, defect.get('location_text')):
            return None
        if not self.field_in_row(defect_sources, 'defect_type_raw', key, defect.get('defect_type_raw')):
            return None
        # This path deliberately does not assign individual facts in compound cells.
        description_cells = [b for b in self.cells(key) if description in b['text']]
        if len(description_cells) != 1 or re.search(r'[;；\n]', description_cells[0]['text']):
            return None
        return key

    def relation_in_row(self, sources, key, values):
        # Every semantic endpoint needs an actual relation quote in this row.
        return key is not None and all(value and any(
            self.key(self.source_block(s)) == key and compact(value) in compact(s['exact_quote'])
            for s in sources) for value in values)

    def bridge_component(self, sources, bridge, component):
        name, code = bridge.get('name'), component.get('component_code')
        name_source = any(self.source_block(s) and name
                          and compact(name) in compact(s['exact_quote']) for s in sources)
        code_source = any(self.source_block(s) and code
                          and compact(code) in compact(s['exact_quote'])
                          and self.component_row(self.key(self.source_block(s)), code) for s in sources)
        return name_source and code_source
