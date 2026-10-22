"""Independent fixed expectations over real outputs; never provided to the model."""
import hashlib
import json
from pathlib import Path

from ontology_proj.adapters.bridge_catalog import BridgeCatalog
from ontology_proj.adapters.business_store import LocalBusinessStore
from ontology_proj.adapters.business_validation import BusinessValidator
from ontology_proj.domain.graph_mapping import project_bundle
from ontology_proj.application.semantic import build_chunks

ROOT=Path(__file__).resolve().parents[2]
CASES=[
    ('shengli','03','6fd07b5e3fe7ff111f09a2e941b133e84b9ba8859cc2071f965b0a807b2aab6c',
     [('3-1#','左侧腹板','2.6'),('3-1#','挑臂','0.9'),('3-2#','挑臂','0.3')]),
    ('datong','02','f929649bbdff6acb0ba602794bd95a3e69fa597dbd15bf5047eac22148b51bed',
     [('3-29#梁','3-29#梁近3#台梁底处','2.25')]),
]


def verify(filename='real-verification.json'):
    result=[]
    for name,version,sha,expected in CASES:
        root=ROOT/f'data/catalog-evidence-20261016-{version}/runtime'
        did='doc_'+sha;store=LocalBusinessStore(root)
        document=store.read_artifact(did,'normalized')
        records=store.read_business_artifact(did,'records')
        validator=BusinessValidator(catalog=BridgeCatalog(root))
        assert not validator.validate_records(records,document)
        assert len(records['bridges'])==1
        components={r['component_id']:r for r in records['components']}
        defects={r['defect_id']:r for r in records['defects']}
        actual=[]
        for observation in records['observations']:
            component=components[observation['component_id']];defect=defects[observation['defect_id']]
            actual.append((component['component_code'],defect['location_text'],observation['area_value']))
            assert defect['component_id']==component['component_id']
            assert defect['defect_type']=='exposed_rebar' and defect['defect_code'] is None
            assert component['component_type'] is None and component['name']==component['component_code']
            assert observation['observed_at'] is None and observation['observation_code'] is None
            assert observation['reported_count']==(None if name=='shengli' else 1)
            assert observation['acceptance_status']=='accepted' and observation['review_status']=='unreviewed'
            assert observation['area_unit']=='m²' and observation['area_is_approximate'] is False
            if name=='datong':
                assert observation['area_expression']=='S=2.5×0.9㎡'
                assert observation['area_original_value'] is None
                assert observation['area_basis']=='explicit_area_product'
            else:assert observation['area_original_value']==observation['area_value'] and observation['area_expression'] is None
        assert sorted(actual)==sorted(expected)
        assert len(components)==(2 if name=='shengli' else 1) and len(defects)==len(expected)
        # Verify every published quote and exact origin independently of the model.
        blocks={b['block_id']:b for b in document['blocks']}
        for e in records['evidence']:
            b=blocks[e['block_id']]
            assert b['text'][slice(*e['charspan'])]==e['quote']
            assert any(l['page_no']==e['page_no'] and all(abs(x-y)<1e-6 for x,y in zip(l['bbox'],e['bbox'])) for l in b['locations'])
        projection=project_bundle({**document,'bridge_binding':validator.bridge_binding(document)},records)
        chunks,excluded=build_chunks(document,records,'offline-contract-check')
        assert {o['observation_id'] for o in records['observations']} <= {r for c in chunks for r in c['record_ids']}
        response=store.read_business_artifact(did,'response')
        result.append(dict(sample=name,document_id=did,run_id=store.read_business(did)['run_id'],
                           expected_observations=expected,actual_observations=actual,component_count=len(components),
                           evidence_count=len(records['evidence']),response_model=response.get('model'),
                           finish_reason=response['choices'][0]['finish_reason'],
                           graph_projection_nodes=sum(len(v) for v in projection['nodes'].values()),
                           local_chunks=len(chunks),formal_publication=True,actual_graph_write=False,embedding_calls=0))
    protected=json.loads((ROOT/'specs/014-catalog-evidence/results/protected-before.json').read_text())
    changed=[k for k,v in protected.items() if hashlib.sha256((ROOT/k).read_bytes()).hexdigest()!=v]
    assert not changed
    receipt=dict(documents=result,protected_files=len(protected),protected_changes=changed,
                 boundary='Real external generation and formal local records; projection/chunking are local only. No graph/embedding/QA/report/browser acceptance.')
    out=ROOT/'specs/014-catalog-evidence/results'/filename
    with out.open('x') as f:json.dump(receipt,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps(receipt,ensure_ascii=False,indent=2))


if __name__=='__main__':
    import sys
    verify(sys.argv[1] if len(sys.argv)>1 else 'real-verification.json')
