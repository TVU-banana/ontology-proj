"""Full text in shared-field rows; source aliases restored before validation."""
import copy
import hashlib
import json
from ontology_proj.adapters.fact_extractor import RelayFactExtractor, canonical
from ontology_proj.domain.registration import ManualRegistration
from ontology_proj.application.files import FileStoreError


class RegisteredFactExtractor(RelayFactExtractor):
    fields = ['block_id','kind','page_no','text','original_text','table_id','row','column',
              'row_span','column_span','column_header','row_header']

    def __init__(self, base, model, key, registry, fallback_models=()):
        super().__init__(base,model,key,fallback_models)
        self.registry = registry
        self.aliases = {};self.prohibited = set()
        self.prompt = self.prompt.replace('schema_version 固定 0.1.0-draft','schema_version 固定 0.3.0-draft')
        self.prompt += '\n受控登记规则优先：本次只正式登记manual_registration列出的桥梁/构件（纸面编号null，不抄内部ID为原编号）。原文其他对象保留为issues/必要原文说明，不扩大登记对象，不宣称全文已完全抽取。'
        self.prompt += '\nblocks使用block_fields规定的数组列，短block_id逐字复制。所有原文完整，已批准registration来源仅固定身份/源归属，不提供测量答案。schema_version输出0.3.0-draft，identity_mode=registered。'
        self.prompt += '\n来源优先复制所属登记窗口中的连续原文，覆盖该字段，保持空格/换行；这条覆盖此前统一复制整个块的策略。不得为腹板引用挑臂数值或反过来。描述可只取自己的连续病害句；位置身份可另引用登记名称/照片说明。原文单位包括m 2，保持原写法。'
        self.prompt += '\n同一位置在正文/表/汇总重复只产生同一Defect/Observation；不要用一个代表替代其他已登记位置。两种支持病害之外不扩展类型；没有明确主梁表述不能输出girder。未见明显病害只能保留negative statement，登记statement_scopes明确绑定构件时按该人工关联引用原句；其他没有明确构件原文则不猜component_ref，以issues保留未映射原句，不造肯定实体。'
        self.prompt += '\n最高优先级：纸面*_code不是local_id，也不是登记键，本受控源没有这些原编号，保持null。位置使用所附原文登记marker，不把3#跨与3-1#位置拼成原文没有的字符串。Observation sources只复制候选Schema限定的登记窗口quote，不复制整个长表格单元格；各字段可重复引用同一合法窗口。严格遵守本次请求Schema的enum/const及来源窗口，不能用领域知识改成girder。'

    def configuration(self):
        return {**super().configuration(),'schema_version':'0.3.0-draft','semantic_revision':'1.3','identity_mode':'registered',
                'max_tokens':64000,'encoding':'shared-fields-full-text-1',
                'identity_registry_sha256':hashlib.sha256(canonical(self.registry).encode()).hexdigest(),
                'controlled_request_constraints':'registration-scope-2'}

    def request(self, document):
        ManualRegistration(self.registry,document)
        base = super().request(document)
        original = json.loads(base['messages'][1]['content'])
        rows = [];reverse = {}
        self.aliases = {};self.prohibited = set()
        for i,b in enumerate(original['blocks_in_document_order'],1):
            alias = 'b'+str(i);self.aliases[alias] = b['block_id'];reverse[b['block_id']] = alias
            rows.append([alias]+[b.get(k) for k in self.fields[1:]])
            source = document['blocks'][i-1]['source_ref']
            if source in self.registry.get('unusable_source_refs',[]): self.prohibited.add(alias)
        registration = copy.deepcopy(self.registry)
        # Do not provide expected types/measurement answers to the model.
        for e in registration['entries']:
            e.pop('allowed_component_type',None)
            for s in e['sources']+e.get('observation_scopes',[])+e.get('statement_scopes',[]):s['block_id'] = reverse[s['block_id']]
        schema = original['candidate_schema']
        defs = schema['$defs']
        component_names = [n for e in self.registry['entries'] if e['kind']=='Component' for n in e['names']]
        types = list(dict.fromkeys(e['allowed_component_type'] for e in self.registry['entries'] if e['kind']=='Component'))
        markers = list(dict.fromkeys(n for e in self.registry['entries'] for n in e.get('defect_location_markers',[])))
        defs['ComponentCandidateData']['properties']['name'] = {'enum':component_names}
        defs['ComponentCandidateData']['properties']['component_type'] = {'enum':types}
        defs['ComponentCandidateData']['properties']['location_text'] = {'enum':[None]+markers}
        defs['ComponentCandidateData'].setdefault('allOf',[]).extend([
            {'if':{'properties':{'name':{'enum':e['names']}}},
             'then':{'properties':{'component_type':{'const':e['allowed_component_type']},
                                   'location_text':{'enum':[None]+e['defect_location_markers']}}}}
            for e in self.registry['entries'] if e['kind']=='Component'])
        defs['DefectCandidateData']['properties']['location_text'] = {'enum':markers}
        for name,field in [('BridgeCandidateData','bridge_code'),('ComponentCandidateData','component_code'),
                           ('DefectCandidateData','defect_code'),('ObservationCandidateData','observation_code')]:
            defs[name]['properties'][field] = {'type':'null'}
        scopes = [s for e in registration['entries'] for s in e.get('observation_scopes',[])]
        rules = [{'properties':{'block_id':{'const':s['block_id']},'exact_quote':{'const':s['quote']}},
                  'required':['block_id','exact_quote']} for s in scopes]
        defs['ObservationCandidate']['properties']['sources']['items']['allOf'].append({'oneOf':rules})
        payload = {'trusted_context':original['trusted_context'],'candidate_schema':schema,
                   'block_fields':self.fields,'blocks_in_document_order':rows,
                   'tables':[{k:t[k] for k in ('table_id','page_no','num_rows','num_cols','header_semantics')} for t in document['tables']],
                   'parser_warnings':original['parser_warnings'],'manual_registration':registration,
                   'prohibited_source_ids':sorted(self.prohibited),'quality_boundaries':self.registry.get('quality_boundaries',[])}
        base['messages'][1]['content'] = canonical(payload)
        return base

    def extract(self, request):
        result = super().extract(request)
        candidate = result.get('candidate')
        if candidate is None:return result
        mapped = copy.deepcopy(candidate);invalid = []
        def visit(value):
            if isinstance(value,list):
                for item in value:visit(item)
            elif isinstance(value,dict):
                if 'block_id' in value:
                    alias = value['block_id']
                    if not isinstance(alias,str) or alias not in self.aliases or alias in self.prohibited:invalid.append(alias)
                    else:value['block_id'] = self.aliases[alias]
                for k,v in value.items():
                    if k != 'block_id':visit(v)
        visit(mapped)
        if invalid:
            result.update(candidate=None,diagnostics=[{'code':'SOURCE_ALIAS_OR_TRUST_INVALID','severity':'reject',
                          'related_local_ids':[],'message':'Unknown or explicitly unusable source'}])
        else:result['candidate'] = mapped
        return result
