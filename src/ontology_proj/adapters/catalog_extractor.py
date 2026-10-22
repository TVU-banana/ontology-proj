"""Per-document extractor routing. Legacy source registration never leaks to new PDFs."""
import json
from ontology_proj.adapters.fact_extractor import RelayFactExtractor,canonical
from ontology_proj.adapters.registered_extractor import RegisteredFactExtractor


class CatalogFactExtractor(RelayFactExtractor):
    def __init__(self,base,model,key,catalog,legacy=None,fallback_models=()):
        super().__init__(base,model,key,fallback_models)
        self.catalog=catalog
        self.legacy=RegisteredFactExtractor(base,model,key,legacy,fallback_models) if legacy else None
        self.legacy_document=legacy['document_id'] if legacy else None
        self.prompt=self.prompt.replace('0.3.0-draft','0.4.0-draft')
        self.prompt+='\n本次identity_mode=catalog。可信bridge_binding仅提供报告归属，不能用其技术ID/官方登记编号填充原文bridge_code。原文无桥梁编号时bridge_code=null，桥名必须有原文来源。构件保留原编号；不套用其他报告对象名单；未知编号/类型/位置不猜。多桥报告不得混成单桥。'
        self.prompt+='''
普通报告语义1.5（以下规则优先于旧提示的上下文猜测）：
1. 同一原构件编号只有一个Component。部位列的腹板、挑臂等是Defect.location_text，不拆成多个同编号Component。Component.name使用原文构件名称；只有编号没有独立名称时完整复制编号作显示名称并引用该编号单元格。Component.location_text无构件整体位置则null，不用某一病害部位代替。不能从“梁”、腹板或上部结构猜girder，配置未支持的类型为null。
2. 原病害/观测编号没有就null。简单表格每条单处病害行分别建Defect及Observation；local_id可由table_id+row稳定命名，不把local_id填入原编号。不同部位不得合并；同一行不要因图注重复新增观测。
3. description完整复制病害描述单元格，不拼接部位或面积单元格。分别为location_text、description、面积值/单位或area_expression提供实际同行块的来源；没有“面积”二字的S=表达也可作为面积来源。位置与测量分列不必出现在description里，但必须同一明确构件行。近似限定、否定与疑似照原文保留。
4. HAS_COMPONENT关系分别引用桥名块及构件编号块；HAS_DEFECT关系分别引用同一病害行的构件编号、部位、病害描述；HAS_OBSERVATION关系引用同一行部位和病害描述。target_field用对应关系白名单（component_code/location_text/description/name等），不能把实体专用类型字段放进关系。来源可多个，每个quote仍为单块连续原文。
5. 一行多事实、合并格、含糊归属不能强行套用简单行规则；保留issues和原文。document_structure仅为解析辅助关系，不是病害事实或照片诊断。
6. reported_count只在原文明确写“1处/一处/其他明确处数”时填写并给同名source；一行、一张照片、一个Defect或single_defect都不是原文处数。未明写处数必须null，不得默认1。
7. defect_type与defect_type_raw是两个不同字段，即使同引“露筋”，也必须分别有target_field=defect_type和target_field=defect_type_raw的来源。逐实体检查每个非null字段的同名来源；缺来源时回到原块补齐合法来源，不能只给原始术语来源。
'''

    def configuration(self):
        return {**super().configuration(),'schema_version':'0.4.0-draft','semantic_revision':'1.5.1','identity_mode':'catalog','configuration_version':'1.4','identity_resolution':'bridge-identity-1'}

    def configuration_for(self,document_id):
        return self.legacy.configuration() if self.legacy and document_id==self.legacy_document else self.configuration()

    def request(self,document):
        if self.legacy and document['document_id']==self.legacy_document:return self.legacy.request(document)
        binding=self.catalog.binding(document)
        request=super().request(document)
        payload=json.loads(request['messages'][1]['content'])
        payload['trusted_context']['bridge_binding']={'bridge_id':binding['bridge_id'],'reason':binding['reason']}
        payload['document_structure']=document.get('structure', {})
        # Put field-source completeness into the requested schema as well as prose.
        # This constrains format only; actual quotes and semantics are still validated.
        definitions=payload['candidate_schema']['$defs']
        for kind in ('Bridge','Component','Defect','Observation'):
            candidate=definitions[kind+'Candidate']
            fields=list(definitions[kind+'CandidateData']['required'])
            if kind=='Observation':fields.append('area_expression')
            for field in fields:
                present={'properties':{field:{'not':{'type':'null'}}},'required':[field]}
                source={'properties':{'target_field':{'const':field}},'required':['target_field']}
                candidate.setdefault('allOf',[]).append({
                    'if':{'properties':{'data':present}},
                    'then':{'properties':{'sources':{'contains':source}}}})
        request['messages'][1]['content']=canonical(payload)
        return request

    def extract(self,request):
        payload=json.loads(request['messages'][1]['content'])
        if self.legacy and payload['trusted_context']['identity_mode']=='registered':return self.legacy.extract(request)
        return super().extract(request)
