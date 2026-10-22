"""Native function-call protocol over the existing relay transport with availability fallback."""
from ontology_proj.adapters.fact_extractor import RelayFactExtractor
from ontology_proj.application.files import FileStoreError


class RelayAgentProvider:
    def __init__(self, base, model, key, fallback_models=()):
        self.transport=RelayFactExtractor(base,model,key,fallback_models)

    def request(self, messages, tools):
        return {'model':self.transport.model,'temperature':0,'max_tokens':2000,
                'thinking':{'type':'disabled'},'stream':False,'tools':tools,
                'tool_choice':'auto','parallel_tool_calls':False,'messages':messages}

    def complete(self, request):
        # extract's candidate decoder is irrelevant here; use the native response.
        result=self.transport.extract(request)
        if result.get('error_code'):
            error = FileStoreError('MODEL_TRANSPORT_FAILED','Agent relay request failed',retryable=False)
            error.provider_response = result['response']
            raise error
        return result['response']
