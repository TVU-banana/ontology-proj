"""Shared local extraction use case, never consumes human golden facts."""
from ontology_proj.application.files import FileStoreError
from ontology_proj.application.ports.business_extraction import FactExtractor, BusinessValidator, BusinessStore


class ExtractBusiness:
    def __init__(self, store: BusinessStore, extractor: FactExtractor, validator: BusinessValidator):
        self.store, self.extractor, self.validator = store, extractor, validator

    def execute(self, document_id, *, fresh=False):
        with self.store.exclusive():
            document = self.store.read_artifact(document_id, 'normalized')
            if not fresh:
                published = self.store.read_business(document_id)
                if published is not None:
                    return {'scope': 'business_extract', 'status': 'succeeded', 'reused': True, **published}
            configuration=self.extractor.configuration_for(document_id) if hasattr(self.extractor,'configuration_for') else self.extractor.configuration()
            run = self.store.begin_business(document_id, configuration, fresh)
            run_id = run['run_id']
            try:
                request = self.extractor.request(document)
                self.store.save_business(document_id, run_id, 'request', request)
                extracted = self.extractor.extract(request)
                self.store.save_business(document_id, run_id, 'response', extracted['response'])
                candidate = extracted['candidate']
                if candidate is None:
                    outcome = {'records': None, 'diagnostics': extracted['diagnostics']}
                else:
                    self.store.save_business(document_id, run_id, 'candidate', candidate)
                    outcome = self.validator.validate(candidate, document)
                records = outcome.pop('records')
                bridge_id = self.validator.bridge_binding(document)['bridge_id'] if records and records.get('identity_mode')=='catalog' else document.get('import_context', {}).get('bridge_id')
                if records is not None and bridge_id is not None and [b['bridge_id'] for b in records['bridges']] != [bridge_id]:
                    outcome['diagnostics'].append({'code': 'BRIDGE_CONTEXT_CONFLICT', 'severity': 'reject', 'related_local_ids': [], 'message': 'Bridge differs from trusted import context'})
                    records = None
                if records is not None:
                    errors = self.validator.validate_records(records, document)
                    if errors:
                        outcome['diagnostics'].extend(errors)
                        records = None
                self.store.save_business(document_id, run_id, 'validation', outcome)
                if records is None:
                    error_code = extracted.get('error_code')
                    run.update(status='failed' if error_code else 'rejected', error_code=error_code or 'CANDIDATE_REJECTED')
                else:
                    self.store.save_business(document_id, run_id, 'records', records)
                    run.update(status='succeeded', error_code=None)
            except Exception as error:
                code = error.code if isinstance(error, FileStoreError) else 'BUSINESS_EXTRACTION_FAILED'
                run.update(status='failed', error_code=code)
            self.store.save_business(document_id, run_id, 'diagnostic', run)
            if run['status'] == 'succeeded':
                try:
                    self.store.publish_business(document_id, run_id)
                except FileStoreError as error:
                    run.update(status='failed', error_code=error.code)
                    self.store.save_business(document_id, run_id, 'diagnostic', run)
            return {'scope': 'business_extract', 'document_id': document_id, 'run_id': run_id, 'status': run['status'], 'error_code': run.get('error_code'), 'reused': False}


class ReadBusiness:
    def __init__(self, store: BusinessStore):
        self.store = store

    def read(self, document_id, artifact='manifest'):
        with self.store.exclusive():
            result = self.store.read_business(document_id)
            if result is None:
                raise FileStoreError('ARTIFACT_NOT_GENERATED', 'Business results have not been published')
            return result if artifact == 'manifest' else self.store.read_business_artifact(document_id, artifact)
