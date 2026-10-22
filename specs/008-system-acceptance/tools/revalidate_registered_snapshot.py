"""Publish new validation of unchanged run04 model data, without generation."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from uuid import uuid4
from ontology_proj.settings import Settings
from ontology_proj.bootstrap import build_validator
from ontology_proj.adapters.ingestion_store import IngestionStore, ProcessLease
from ontology_proj.application.business_extraction import ExtractBusiness
from ontology_proj.application.dto import ImportContext
from ontology_proj.domain.source import SourceKind, DocumentKind
from real_registered import ROOT, SOURCE, sha, write

OUT=ROOT/'specs/008-system-acceptance/results/real-shengli-revalidated-20261012-01'
DATA=ROOT/'data/s52-real-shengli-revalidated-20261012-01'
PRIOR=ROOT/'data/s52-real-shengli-20261012-04'


def main():
    assert not (OUT/'result.json').exists()
    OUT.mkdir(parents=True,exist_ok=True);DATA.mkdir(parents=True,exist_ok=True)
    prior=Settings(_env_file=PRIOR/'private/.env.app');oldstore=IngestionStore(prior.data_root)
    er=json.loads((ROOT/'specs/008-system-acceptance/results/real-shengli-20261012-04/extraction-result.json').read_text())
    did=er['document_id'];oldrun=prior.data_root/'documents'/did/'business-runs'/er['run_id']
    inputs={name:json.loads((oldrun/(name+'.json')).read_text()) for name in ['candidate','response','request']}
    protected=[p for p in PRIOR.rglob('*') if p.is_file()]
    write(DATA/'protected-source-run.json',{str(p.relative_to(ROOT)):sha(p) for p in protected})
    registry=DATA/'runtime/registrations/shengli.json';registry.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(prior.identity_registry_path,registry)
    s=prior.model_copy(update={'data_root':DATA/'runtime','identity_registry_path':registry,
                              'identity_registry_sha256':sha(registry),'actor_id':'s52_real_revalidated'})
    private=DATA/'private';private.mkdir(exist_ok=True)
    lines=[]
    for key,value in s.model_dump().items():
        if hasattr(value,'get_secret_value'):value=value.get_secret_value()
        if value is None:continue
        lines.append(key.upper()+'='+json.dumps(str(value),ensure_ascii=False))
    env=private/'.env.app';env.write_text('\n'.join(lines)+'\n');env.chmod(0o600)
    shutil.copyfile(PRIOR/'private/.env.neo4j',private/'.env.neo4j');(private/'.env.neo4j').chmod(0o600)
    s=Settings(_env_file=env);store=IngestionStore(s.data_root);lease=ProcessLease(store);lease.acquire()
    try:
        context=ImportContext(DocumentKind.INSPECTION_REPORT,SourceKind.REAL,oldstore.read_source(did).context.bridge_id)
        store.save_source((SOURCE/'input/shengli-original.pdf').read_bytes(),'shengli-original.pdf',context)
        manifest=oldstore.read_published(did);now=datetime.now(timezone.utc).isoformat()
        op={'operation_id':str(uuid4()),'document_id':did,'scope':'local_parse','status':'running','stage':'publishing',
            'error_code':None,'created_at':now,'updated_at':now,'configuration':manifest['configuration'],'artifact_keys':[]}
        store.save_operation(op)
        for name in ['docling','normalized']:store.save_artifact(did,name,oldstore.read_artifact(did,name))
        store.publish(did,op['operation_id'],op['configuration'],manifest['versions']);op.update(status='succeeded_with_warnings',stage='published');store.save_operation(op)
        class SavedActualOutput:
            def configuration(self):
                return {'mode':'unchanged_actual_model_snapshot_new_validation','source_run_id':er['run_id'],
                        'source_request_sha256':sha(oldrun/'request.json'),'source_response_sha256':sha(oldrun/'response.json'),
                        'schema_version':'0.2.0-draft','source_scope_location_rule':'bound-complete-pattern-1','new_generation_calls':0}
            def request(self,document):return copy.deepcopy(inputs['request'])
            def extract(self,request):return {'candidate':copy.deepcopy(inputs['candidate']),
                                             'response':copy.deepcopy(inputs['response']),'diagnostics':[]}
        result=ExtractBusiness(store,SavedActualOutput(),build_validator(s)).execute(did)
        write(OUT/'result.json',{**result,'new_generation_calls':0,'new_docling_calls':0,'origin':'run04 actual complete model output, unchanged snapshot',
             'candidate_bytes_same_as_original':result['status']=='succeeded' and (oldrun/'candidate.json').read_bytes()==(s.data_root/'documents'/did/'business-runs'/result['run_id']/'candidate.json').read_bytes(),
             'original_run_remains_rejected':True,'professional_review':'not_run'})
        print(json.dumps(result,ensure_ascii=False))
    finally:lease.release()


if __name__=='__main__':main()
