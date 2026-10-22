"""Post-call validation and independent comparison; never used in model inputs."""
import hashlib
import json
import re
import sys
from pathlib import Path
from datetime import datetime, timezone

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'experiments/business-extraction-001'))
from validate import validate_candidate, validate_records


def compact(text):
    return re.sub(r'\s+', '', text)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def compare(records, document, expected, common):
    checks = []
    def check(name, passed, actual=None, wanted=None):
        checks.append({'name': name, 'passed': bool(passed), 'actual': actual, 'expected': wanted})
    counts = {k: len(records[k]) for k in ('bridges','components','defects','observations')}
    want = {k: expected['counts'][key] for k,key in [('bridges','bridges'),('components','components'),('defects','defects'),('observations','positive_observations')]}
    check('object_counts', counts == want, counts, want)
    bridges = {b['bridge_id']:b['bridge_code'] for b in records['bridges']}
    check('bridge_code_and_name', sorted((b['bridge_code'],b['name']) for b in records['bridges']) == [(common['bridge']['bridge_code'],common['bridge']['name'])])
    components = {c['component_id']:c['component_code'] for c in records['components']}
    actual = sorted((c['component_code'],c['name'],c['component_type'],compact(c['location_text'] or ''),bridges.get(c['bridge_id'])) for c in records['components'])
    wanted = sorted((c['component_code'],c['name'],c['component_type'],compact(c['location_text']),c['bridge_code']) for c in common['components'])
    check('component_fields_and_parent',actual==wanted,actual,wanted)
    defects = {d['defect_id']:d for d in records['defects']}
    want_defects = {f['defect_code']:f for f in expected['positive_facts']}
    actual_defects = sorted((d['defect_code'],components.get(d['component_id']),d['defect_type'],d['defect_type_raw'],compact(d['location_text'] or ''),d['identity_scope']) for d in records['defects'])
    expected_defects = sorted((k,f['component_code'],f['defect_type'],f['defect_type_raw'],compact(f['location_text']),common['defect_identity_scope']) for k,f in want_defects.items())
    check('defect_fields_and_parent',actual_defects==expected_defects,actual_defects,expected_defects)
    observations = {o['observation_code']:o for o in records['observations']}
    check('observation_codes_unique_exact', len(observations)==len(records['observations']) and set(observations)=={f['observation_code'] for f in expected['positive_facts']})
    projection=[]
    for f in expected['positive_facts']:
        code=f['observation_code'];o=observations.get(code)
        if o is None:
            check(code+'_all_fields',False,None,f);continue
        d=defects.get(o['defect_id'],{})
        actual={k:o[k] for k in ('observed_at','assertion','scope','reported_count','area_original_value','area_original_unit','area_value','area_unit','area_conversion','area_is_approximate','acceptance_status')}
        actual.update(defect_code=d.get('defect_code'),component_code=components.get(o['component_id']),description_text=compact(o['description']),missing_fields=sorted(o['missing_fields']),source_kind=o['source_kind'])
        wanted={k:f[k] for k in ('observed_at','assertion','scope','reported_count','area_original_value','area_original_unit','area_value','area_unit','area_conversion','area_is_approximate','acceptance_status')}
        wanted.update(defect_code=f['defect_code'],component_code=f['component_code'],description_text=compact(f['description_text']),missing_fields=sorted(f['missing_fields']),source_kind='synthetic')
        check(code+'_all_fields',actual==wanted,actual,wanted)
        projection.append({'observation_code':code,**actual})
    all_records=sum((records[k] for k in ('bridges','components','defects','observations')),[])
    states={s:sum(r['acceptance_status']==s for r in all_records) for s in ('accepted','pending_review','rejected')}
    expected_states={'accepted':expected['counts']['accepted_business_records'],'pending_review':expected['counts']['pending_review_business_records'],'rejected':0}
    check('acceptance_routing',states==expected_states,states,expected_states)
    check('common_record_states',all(all(r[k]==common[k] for k in ('validation_status','review_status','quality_mode','quality_score')) for r in all_records))
    check('report_date_never_observation_date',all(o['observed_at'] is None and 'observed_at' in o['missing_fields'] for o in records['observations']))
    negatives=[s for s in records['statements'] if s['kind']=='negative']
    check('negative_count',len(negatives)==expected['counts']['negative_statements'],len(negatives),expected['counts']['negative_statements'])
    for i,n in enumerate(expected['negative_statements'],1):
        hits=[s for s in negatives if compact(s['text'])==compact(n['text']) and components.get(s['component_ref'])==n['component_code'] and compact(s['location_text'] or '') in {compact(t) for t in n.get('allowed_location_text',[n['location_text']])}]
        check(f'negative_{i}_text_component_location',len(hits)==1)
    check('other_statements_only_unmapped',all(s['kind'] in ('negative',common['allowed_other_statement_kind']) for s in records['statements']))
    check('generated_records_validate',not validate_records(records,document))
    return {'checks':checks,'passed':sum(c['passed'] for c in checks),'failed':sum(not c['passed'] for c in checks),'core_projection':projection,'evidence_count':len(records['evidence']),'counts':counts,'acceptance_counts':states}


def main():
    config=json.loads((HERE/'baseline.json').read_text())
    expected=json.loads((ROOT/config['acceptance_reference']['expected_path']).read_text())
    assert sha(ROOT/config['acceptance_reference']['expected_path'])==config['acceptance_reference']['expected_sha256']
    reference=json.loads((ROOT/'experiments/business-extraction-001/results/integrity-check.json').read_text())['sha256']
    validator_path='experiments/business-extraction-001/validate.py'
    assert sha(ROOT/validator_path)==reference[validator_path], 'Validator behavior changed'
    assert sha(ROOT/'specs/001-business-extraction/business.schema.json')==config['frozen_artifacts_sha256']['frozen/business.schema.json']
    cases={c['case_id']:c for c in expected['cases']}
    results=[]
    for case in config['cases']:
        for run_id in case['planned_run_ids']:
            out=HERE/'results'/run_id
            run=json.loads((out/'run.json').read_text())
            summary={'run_id':run_id,'case_id':case['case_id'],'call_status':run['status'],'status':'failed','checks':[],'passed':0,'failed':1}
            if (out/'candidate.json').exists():
                candidate=json.loads((out/'candidate.json').read_text())
                document=json.loads((out/'input-document.json').read_text())
                try:
                    validation=validate_candidate(candidate,document)
                    records=validation.pop('records')
                    validation['status']='records_generated' if records is not None else 'candidate_retained_without_records'
                    save(out/'validation.json',validation)
                    if records is not None:
                        save(out/'records.json',records)
                        summary.update(compare(records,document,cases[case['case_id']]['expected'],expected['common']))
                        summary['status']='passed' if summary['failed']==0 else 'fact_comparison_failed'
                    else:
                        summary.update(status='validator_rejected',diagnostics=validation['diagnostics'])
                except (KeyError,TypeError,ValueError) as e:
                    summary.update(status='validator_exception',error=str(e))
            else:
                summary.update(status='no_decodable_candidate',error=run.get('error'))
            summary.update(checked_at=datetime.now(timezone.utc).isoformat(),validator_sha256=reference[validator_path],expectations_sha256=config['acceptance_reference']['expected_sha256'],repair_from=None)
            save(out/'evaluation.json',summary)
            results.append(summary)
            print(json.dumps({k:summary.get(k) for k in ('run_id','status','passed','failed','evidence_count')},ensure_ascii=False))
    pairs=[]
    for case in config['cases']:
        pair=[r for r in results if r['case_id']==case['case_id']]
        both=all(r['status']=='passed' for r in pair)
        pairs.append({'case_id':case['case_id'],'both_calls_passed':both,'passing_calls':sum(r['status']=='passed' for r in pair),'core_facts_same_between_calls':pair[0].get('core_projection')==pair[1].get('core_projection') if both else None})
    save(HERE/'results/evaluation.json',{'baseline_id':config['baseline_id'],'attempted':len(results),'passed_calls':sum(r['status']=='passed' for r in results),'failed_calls':sum(r['status']!='passed' for r in results),'both_passed_cases':sum(p['both_calls_passed'] for p in pairs),'cases':pairs,'runs':results,'automatic_repairs':0,'limits':['Five synthetic selectable-text documents, two first-pass calls each','No database, OCR, complex originals or statistical reliability claim']})


if __name__=='__main__':main()
