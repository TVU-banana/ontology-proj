"""Single rule resource loader. Domain receives immutable copied plain rules."""
import hashlib
from importlib.resources import files
import json
from ontology_proj.domain.measurement import MeasurementRules


def load_rules():
    raw=files('ontology_proj.resources').joinpath('business-rules.json').read_bytes()
    rules=MeasurementRules(json.loads(raw))
    rules.sha256=hashlib.sha256(raw).hexdigest()
    return rules
