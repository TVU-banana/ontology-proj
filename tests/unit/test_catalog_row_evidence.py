"""Saved real parse inputs + explicitly hand-built candidates, never model acceptance."""
import copy
import json
from pathlib import Path

import pytest

from ontology_proj.adapters.bridge_catalog import BridgeCatalog
from ontology_proj.adapters.business_validation import BusinessValidator
from ontology_proj.adapters.catalog_extractor import CatalogFactExtractor

FIXTURES = Path(__file__).resolve().parents[2] / 'specs/014-catalog-evidence/fixtures'


def case(name, tmp_path):
    document = json.loads((FIXTURES / f'{name}-normalized.json').read_text())
    blocks = document['blocks']
    def cell(row, col, table='#/tables/1'):
        return next(b for b in blocks if b.get('table_id') == table and b.get('row') == row and b.get('column') == col)
    def source(block, field):
        return dict(block_id=block['block_id'], exact_quote=block['text'], target_field=field, occurrence_hint=None)
    bridge = cell(0, 1, '#/tables/0')
    catalog = BridgeCatalog(tmp_path)
    registered = catalog.register(dict(name=bridge['text'],location='浙江省平湖市胜利路' if name=='shengli' else '浙江省宁波市北仑区泰山路',source_kind='real',official_code=None,official_namespace=None))['bridge']
    catalog.resolve(document, registered['bridge_id'])
    entities = [dict(entity_type='Bridge',local_id='bridge',data=dict(bridge_code=None,name=bridge['text']),sources=[source(bridge,'name')])]
    relations = []
    components = {}
    for row in ([1,2,3] if name == 'shengli' else [1]):
        code = cell(row,0)
        if code['text'] not in components:
            cid = 'component-'+str(row);components[code['text']]=cid
            entities.append(dict(entity_type='Component',local_id=cid,data=dict(component_code=code['text'],name=code['text'],component_type=None,location_text=None),sources=[source(code,'component_code'),source(code,'name')]))
            relations.append(dict(relation_type='HAS_COMPONENT',from_ref='bridge',to_ref=cid,sources=[source(bridge,'name'),source(code,'component_code')]))
        cid=components[code['text']];did='defect-'+str(row);oid='observation-'+str(row)
        description=cell(row,2 if name=='shengli' else 1)
        position=cell(row,1)
        location=position['text'] if name=='shengli' else '近3#台梁底处'
        raw=description['text'] if name=='shengli' else '锈胀露筋'
        entities.append(dict(entity_type='Defect',local_id=did,data=dict(defect_code=None,defect_type='exposed_rebar',defect_type_raw=raw,location_text=location),sources=[source(description,'defect_type'),source(description,'defect_type_raw'),source(position,'location_text')]))
        data=dict(observation_code=None,observed_at=None,assertion='positive',scope='single_defect',reported_count=None if name=='shengli' else 1,description=description['text'],area_original_value=['2.6','0.9','0.3'][row-1] if name=='shengli' else None,area_original_unit='㎡' if name=='shengli' else None,area_is_approximate=False,area_expression=None if name=='shengli' else description['text'])
        sources=[source(description,f) for f in ('description','assertion','scope')]
        measure=cell(row,3) if name=='shengli' else description
        sources.extend(source(measure,f) for f in (('area_original_value','area_original_unit','area_is_approximate') if name=='shengli' else ('area_expression','area_is_approximate','reported_count')))
        entities.append(dict(entity_type='Observation',local_id=oid,data=data,sources=sources))
        relations.append(dict(relation_type='HAS_DEFECT',from_ref=cid,to_ref=did,sources=[source(code,'component_code'),source(position,'location_text'),source(description,'description')]))
        relations.append(dict(relation_type='HAS_OBSERVATION',from_ref=did,to_ref=oid,sources=[source(position,'location_text'),source(description,'description')]))
    candidate=dict(schema_version='0.4.0-draft',identity_mode='catalog',stage='candidate',document_id=document['document_id'],source_kind='real',entities=entities,relations=relations,statements=[],issues=[])
    return document,candidate,BusinessValidator(catalog=catalog)


@pytest.mark.parametrize('name,counts,areas',[('shengli',(2,3),['2.6','0.9','0.3']),('datong',(1,1),['2.25'])])
def test_simple_real_parse_with_hand_built_candidate(name,counts,areas,tmp_path):
    d,c,v=case(name,tmp_path);r=v.validate(c,d)
    assert r['records'] is not None,r['diagnostics']
    b=r['records'];assert (len(b['components']),len(b['observations']))==counts
    assert [o['area_value'] for o in b['observations']]==areas
    assert all(o['observed_at'] is None and o['observation_code'] is None for o in b['observations'])
    assert not v.validate_records(b,d)


@pytest.mark.parametrize('mutation',['wrong_row','wrong_parent','forged_quote','missing_bridge_quote','wrong_relation','merged_cell','compound_cell','substring_code','cross_table','duplicate_observation'])
def test_unsafe_candidate_rejected(mutation,tmp_path):
    d,c,v=case('shengli',tmp_path)
    obs=[e for e in c['entities'] if e['entity_type']=='Observation']
    components=[e for e in c['entities'] if e['entity_type']=='Component']
    if mutation=='wrong_row':
        s=next(s for s in obs[0]['sources'] if s['target_field']=='area_original_value')
        s.update(next(s for s in obs[1]['sources'] if s['target_field']=='area_original_value'))
        obs[0]['data']['area_original_value']='0.9'
    elif mutation=='wrong_parent':
        next(r for r in c['relations'] if r['relation_type']=='HAS_DEFECT')['from_ref']=components[1]['local_id']
    elif mutation=='forged_quote':obs[0]['sources'][0]['exact_quote']='伪造原文'
    elif mutation=='missing_bridge_quote':c['relations'][0]['sources']=c['relations'][0]['sources'][1:]
    elif mutation=='wrong_relation':
        rs=[r for r in c['relations'] if r['relation_type']=='HAS_OBSERVATION'];rs[0]['sources']=copy.deepcopy(rs[1]['sources'])
    elif mutation in ('merged_cell','compound_cell','cross_table'):
        bid=obs[0]['sources'][0]['block_id'];b=next(b for b in d['blocks'] if b['block_id']==bid)
        if mutation=='merged_cell':b['row_span']=2
        elif mutation=='cross_table':b['table_id']='#/tables/0'
        else:b['text']+='；另有一处露筋，S=9㎡'
        old=v.catalog.list()[0]
        catalog=BridgeCatalog(tmp_path/'variant')
        selected=catalog.register({k:old.get(k) for k in ('name','location','source_kind','official_code','official_namespace')})['bridge']
        catalog.resolve(d,selected['bridge_id']);v=BusinessValidator(catalog=catalog)
    elif mutation=='substring_code':components[0]['data']['component_code']='1#'
    else:
        clone=copy.deepcopy(obs[0]);clone['local_id']='another-observation';c['entities'].append(clone)
        r=copy.deepcopy(next(r for r in c['relations'] if r['to_ref']==obs[0]['local_id']));r['to_ref']=clone['local_id'];c['relations'].append(r)
    assert v.validate(c,d)['records'] is None


def test_records_wrong_parent_and_measurement_row_rejected(tmp_path):
    d,c,v=case('shengli',tmp_path);records=v.validate(c,d)['records'];assert records
    original=copy.deepcopy(records)
    records['observations'][0]['component_id']=records['components'][1]['component_id']
    assert v.validate_records(records,d)
    records=original
    records['observations'][0]['evidence_ids']=records['observations'][1]['evidence_ids']
    assert v.validate_records(records,d)


def test_request_structure_and_contract(tmp_path):
    d,c,v=case('shengli',tmp_path)
    extractor=CatalogFactExtractor('http://example.invalid/v1','model','secret',v.catalog)
    payload=json.loads(extractor.request(d)['messages'][1]['content'])
    assert payload['document_structure']==d['structure']
    assert extractor.configuration()['semantic_revision']=='1.5.1'
    assert '同一原构件编号只有一个Component' in extractor.prompt
    from jsonschema import Draft202012Validator
    check=Draft202012Validator(payload['candidate_schema'])
    assert not list(check.iter_errors(c))
    defect=next(e for e in c['entities'] if e['entity_type']=='Defect')
    defect['sources']=[s for s in defect['sources'] if s['target_field']!='defect_type']
    assert list(check.iter_errors(c))


def test_invented_count_and_missing_type_source_still_rejected(tmp_path):
    d,c,v=case('shengli',tmp_path)
    next(e for e in c['entities'] if e['entity_type']=='Observation')['data']['reported_count']=1
    defect=next(e for e in c['entities'] if e['entity_type']=='Defect')
    defect['sources']=[s for s in defect['sources'] if s['target_field']!='defect_type']
    r=v.validate(c,d)
    assert r['records'] is None
    assert any('reported_count' in e['message'] for e in r['diagnostics'])
    assert any('defect_type' in e['message'] for e in r['diagnostics'])


def test_other_paragraph_cannot_supply_row_count(tmp_path):
    d,c,v=case('shengli',tmp_path)
    block=copy.deepcopy(d['blocks'][0]);block['block_id']='unrelated';block['text']='另一部位露筋1处'
    block['source_ref']='test-unrelated';block['locations'][0]['charspan']=[0,len(block['text'])]
    d['blocks'].append(block)
    old=v.catalog.list()[0];catalog=BridgeCatalog(tmp_path/'variant')
    selected=catalog.register({k:old.get(k) for k in ('name','location','source_kind','official_code','official_namespace')})['bridge']
    catalog.resolve(d,selected['bridge_id']);v=BusinessValidator(catalog=catalog)
    observation=next(e for e in c['entities'] if e['entity_type']=='Observation')
    observation['data']['reported_count']=1
    observation['sources'].append(dict(block_id='unrelated',exact_quote=block['text'],target_field='reported_count',occurrence_hint=None))
    r=v.validate(c,d)
    assert r['records'] is None and any(x['code']=='FIELD_ROW_MISMATCH' for x in r['diagnostics'])
