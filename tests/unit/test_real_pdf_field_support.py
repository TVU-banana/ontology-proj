"""Actual-language boundaries, with independent counterexamples; no provider."""
from types import SimpleNamespace
import pytest
from ontology_proj.domain.business import supports, UNITS


def evidence(text, field):
    return [{'block_id': 'paragraph', 'target_field': field, 'exact_quote': text}]


def index(text):
    return SimpleNamespace(blocks={'paragraph': {'kind': 'paragraph', 'text': text}},
                           context=lambda _: text)


@pytest.mark.parametrize('text,value,accepted', [
    ('存在1处坑槽 S=0.04m2', '0.04', True),
    ('存在1处坑槽 S=0.04m 2', '0.04', True),
    ('存在1处坑槽 S=0.0404m2', '0.04', False),
    ('3跨存在1处坑槽 S=0.04m2', '3', False),
    ('存在1处坑槽 S=0.04m2', '1', False),
    ('S=1m2；其他数据0.04', '0.04', False),
    ('S=1e3', '1', False),
    ('S=0.04m2x', '0.04', False),
])
def test_area_number_adjacent_to_unit_does_not_take_other_values(text, value, accepted):
    assert supports('area_original_value', value, evidence(text, 'area_original_value'), index(text)) is accepted


@pytest.mark.parametrize('text,accepted', [
    ('经检查，存在1处坑槽', True),
    ('经检查，存在 1 处坑槽', True),
    ('经检查，疑似存在1处坑槽', False),
    ('未见存在1处坑槽', False),
    ('存在2处坑槽', False),
])
def test_positive_exists_preserves_negative_suspected_count_boundaries(text, accepted):
    assert supports('assertion', 'positive', evidence(text, 'assertion'), index(text)) is accepted


@pytest.mark.parametrize('text,value,accepted', [
    ('S=0.04m 2', False, True),
    ('S=约0.04m 2', False, False),
    ('S=约0.04m 2', True, True),
    ('S<0.04m 2', False, False),
    ('L=4m', False, False),
])
def test_area_approximation_layout_spacing_preserves_qualifiers(text, value, accepted):
    assert supports('area_is_approximate', value, evidence(text, 'area_is_approximate'), index(text)) is accepted


def test_original_unit_allowlist_not_silently_expanded():
    assert UNITS == {'m²', '㎡', 'm2'}
    assert 'm 2' not in UNITS
