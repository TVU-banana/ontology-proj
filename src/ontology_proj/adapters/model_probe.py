"""Uncharged model-list readiness check; generation ability is tested separately."""
import json
import subprocess
from ontology_proj.application.dto import CapabilityState, CapabilityStatus


class RelayModelProbe:
    def __init__(self, base_url, model, key, fallback_models=()):
        self.models = (model, *fallback_models)
        self.base, self.model, self.key = base_url.rstrip('/'), model, key

    def check(self):
        configured = bool(self.base and self.model and self.key)
        if not configured:
            return CapabilityStatus(CapabilityState.NOT_CONFIGURED, False)
        def quote(value):
            return '"'+value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r')+'"'
        config = 'url = '+quote(self.base+'/models')+'\nheader = '+quote('Authorization: Bearer '+self.key)+'\n'
        try:
            result = subprocess.run(['curl','--config','-','--silent','--fail','--noproxy','*','--connect-timeout','2','--max-time','3'], input=config, text=True, capture_output=True, timeout=4)
            models = {m['id'] for m in json.loads(result.stdout)['data']} if result.returncode == 0 else set()
            if not any(model in models for model in self.models):
                return CapabilityStatus(CapabilityState.UNAVAILABLE, True, 'MODEL_NOT_ADVERTISED')
            return CapabilityStatus(CapabilityState.READY, True)
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
            return CapabilityStatus(CapabilityState.UNAVAILABLE, True, 'MODEL_PROBE_FAILED')
