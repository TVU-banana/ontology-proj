"""JSON Schema SDK boundary; domain receives plain data and a check callback."""
import json
from importlib.resources import files

from jsonschema import Draft202012Validator, FormatChecker

from ontology_proj.domain.business import validate_candidate, validate_records, issue
from ontology_proj.adapters.business_rules import load_rules


class BusinessValidator:
    def __init__(self, registration=None, catalog=None):
        self.catalog = catalog
        self.registration = registration
        self.rules = load_rules()
        self.schema = json.loads(files('ontology_proj.resources').joinpath('business.schema.json').read_text())
        Draft202012Validator.check_schema(self.schema)
        self._validator = Draft202012Validator(self.schema, format_checker=FormatChecker())

    def bridge_binding(self, document):
        if self.catalog is None:raise ValueError('Bridge catalog required')
        return self.catalog.binding(document)

    def structure_errors(self, value):
        return ['SCHEMA_INVALID' for _ in self._validator.iter_errors(value)]

    def validate(self, candidate, document):
        try:
            if self.catalog and self.catalog.status(document)['status']=='resolved' and candidate.get('schema_version')!='0.4.0-draft':
                return {'records':None,'diagnostics':[issue('CATALOG_VERSION_REQUIRED','Resolved report requires catalog envelope')]}
            return validate_candidate(candidate, document, self.schema, self.structure_errors, self.registration, self.rules, self.bridge_binding(document) if candidate.get('schema_version')=='0.4.0-draft' else None)
        except (KeyError, ValueError, TypeError, IndexError, ArithmeticError):
            return {'records': None, 'diagnostics': [issue('VALIDATION_INPUT_INVALID', 'Input cannot be validated safely')]}

    def validate_records(self, records, document):
        try:
            if self.catalog and self.catalog.status(document)['status']=='resolved' and records.get('schema_version')!='0.4.0-draft':
                return [issue('RECORD_CATALOG_VERSION_REQUIRED','Resolved report requires catalog envelope')]
            return validate_records(records, document, self.schema, self.structure_errors, self.registration, self.rules, self.bridge_binding(document) if records.get('schema_version')=='0.4.0-draft' else None)
        except (KeyError, ValueError, TypeError, IndexError, ArithmeticError):
            return [issue('RECORD_INPUT_INVALID', 'Records cannot be validated safely')]
