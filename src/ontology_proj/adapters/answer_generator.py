"""Configured relay generation with availability fallback; extraction prompt/request are not used."""
from importlib.resources import files
import json
from ontology_proj.adapters.fact_extractor import RelayFactExtractor
from ontology_proj.application.files import FileStoreError

class RelayAnswerGenerator:
    def __init__(self,base,model,key,fallback_models=()):
        self.transport=RelayFactExtractor(base,model,key,fallback_models)
        self.prompt=files('ontology_proj.resources').joinpath('answer-prompt.txt').read_text()
        self.calls=0

    def request(self,question,context):
        content=json.dumps({'question':question,'provided_context':context},ensure_ascii=False)
        if len(content)>24000:
            raise FileStoreError('MODEL_CONTEXT_TOO_LARGE','Answer payload exceeds the configured input budget')
        return {'model':self.transport.model,'temperature':0,'max_tokens':6000,'thinking':{'type':'disabled'},
            'stream':False,'response_format':{'type':'json_object'},'messages':[
                {'role':'system','content':self.prompt},
                {'role':'user','content':content}]}

    def generate(self,request):
        self.calls+=1
        return self.transport.extract(request)
