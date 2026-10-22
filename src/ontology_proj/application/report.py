"""Deterministic, field-cited report snapshots; no new extraction or generation."""
from copy import deepcopy
from datetime import datetime, timezone
from queue import Queue
from threading import Thread, RLock
from urllib.parse import quote
from uuid import uuid4, UUID
import html
import json

from ontology_proj.application.files import FileStoreError
from ontology_proj.application.query import validate as validate_query, number
from ontology_proj.domain.business import compact, UNITS
from ontology_proj.domain.measurement import record_area_valid

TEMPLATE = 'bridge_inspection_v1'
SECTIONS = [('scope','任务范围'),('sources','资料清单'),('observations','病害与观测汇总'),('limitations','缺失/冲突说明'),('evidence_index','证据索引')]
FIELDS = ('observation_code','observed_at','description','assertion','scope','reported_count','source_kind','area_original_value','area_original_unit','area_value','area_unit','area_conversion','area_is_approximate','missing_fields','review_status','area_expression','area_basis','area_derivation','area_rules_version')
METADATA = {'source_kind','missing_fields','review_status','area_conversion','area_basis','area_derivation','area_rules_version'}


def invalid(message):
    raise FileStoreError('INVALID_REQUEST', message)


def identity(value):
    try:
        if str(UUID(value)) != value: raise ValueError()
    except (ValueError, TypeError, AttributeError):
        invalid('Invalid report ID')
    return value


def request_data(request):
    allowed = {'bridge_id','component_ids','document_ids','date_from','date_to','template_id','expert_requirements','review_mode','include_synthetic'}
    if not isinstance(request,dict) or set(request)-allowed: invalid('Unknown report fields')
    request = deepcopy(request)
    if not isinstance(request.get('bridge_id'),str) or not request['bridge_id']: invalid('Bridge ID is required')
    if request.pop('template_id',TEMPLATE) != TEMPLATE: invalid('Unsupported report template')
    if request.pop('expert_requirements','') != '': invalid('Use structured scope; free-text requirements are not supported')
    scope = validate_query({'query_type':'record_by_id','record_id':'scope-validation','scope':request})['scope']
    return {'scope':scope,'template_id':TEMPLATE,'expert_requirements':''}


def text(value):
    value = '未知' if value is None else str(value)
    # Encode Markdown punctuation before inserting in cells/paragraphs.
    value=value.replace('\r',' ').replace('\n',' / ')
    return ''.join('&#'+str(ord(c))+';' if c in '\\`*_{}[]()|#!' else html.escape(c,quote=True) for c in value)


def refs(ids,labels):
    return ' '.join(f'[证据 {labels[eid]}](/evidence/{quote(eid,safe="")})' for eid in ids)


def render(report):
    sections = {s['section_id']:s for s in report['sections']}
    labels={r['evidence_id']:i+1 for i,r in enumerate(sections['evidence_index']['rows'])}
    lines = ['# 病害与观测汇总', '', f'报告：{report["report_id"]}；模板：{TEMPLATE} / {report["template_version"]}',
             f'生成时间：{report["generated_at"]}（不是观测日期）', '基础检查：通过；专业审核：未执行（not_run）', '']
    for sid,title in SECTIONS:
        lines.extend(['## '+title,''])
        if sid=='scope':
            names={'bridge_id':'桥梁','component_ids':'构件范围','document_ids':'资料范围','date_from':'观测日期起','date_to':'观测日期止','review_mode':'复核过滤','include_synthetic':'包含合成来源'}
            lines.extend(f'- {names[k]}：{text(json.dumps(v,ensure_ascii=False))}' for k,v in report['scope'].items())
        elif sid=='sources':
            for row in sections[sid]['rows']:
                lines.append(f'- {text(row["filename"])}；{text(row["document_id"])}；来源：{text(row["source_kind"])}；{text(row["processing_status"])}')
            if not sections[sid]['rows']: lines.append('无符合范围的已发布资料。')
        elif sid=='observations':
            lines.extend(['| 观测编号（对象 ID 见下方） | 原文描述 | 观测日期 | 原面积 | 规范面积 | 限定 / 范围 / 数量 | 来源 / 复核 | 引用 |', '|---|---|---|---|---|---|---|---|'])
            for row in sections[sid]['rows']:
                v=row['values'];qualifier='近似' if v['area_is_approximate'] is True else '未知近似性' if v['area_is_approximate'] is None else '无近似限定'
                original=text(v.get('area_expression')) if v.get('area_expression') else text(v['area_original_value'])+' '+text(v['area_original_unit'])
                basis='（尺寸推算）' if v.get('area_basis')=='rectangle_dimensions' else '（原文面积式计算）' if v.get('area_basis')=='explicit_area_product' else ''
                lines.append('| '+' | '.join([text(v['observation_code']),text(v['description']),text(v['observed_at']),original,text(v['area_value'])+' '+text(v['area_unit'])+basis,text(str(v['assertion'])+' / '+str(v['scope'])+' / '+str(v['reported_count'])+' / '+qualifier),text(str(v['source_kind'])+' / '+str(v['review_status'])),refs(row['evidence_ids'],labels)])+' |')
            if not sections[sid]['rows']: lines.append('无符合范围的已接纳观测。')
            for row in sections[sid]['rows']:lines.append('- '+text(row['values']['observation_code'])+'：构件 '+text(row['component_id'])+'；病害 '+text(row['defect_id'])+'；观测 '+text(row['observation_id']))
        elif sid=='limitations':
            lines.extend('- '+text(v) for v in report['missing_information']+report['warnings'])
            lines.append('冲突：未执行自动冲突判定；空列表不代表不存在冲突。')
            for row in sections[sid]['rows']: lines.append('- 原文说明：'+text(row['quote'])+'；性质：'+text(row['kind'])+' '+refs([row['evidence_id']],labels))
        else:
            for row in sections[sid]['rows']:
                lines.append('- '+refs([row['evidence_id']],labels)+f'（{text(row["evidence_id"])}）；{text(row["filename"])} / {text(row["document_id"])}；第 {text(row["page_no"])} 页；{text(row["source_kind"])}；字段 {text(row["target_field"])}：'+text(row['quote']))
        lines.append('')
    return '\n'.join(lines)


class GenerateReport:
    def __init__(self, lookup, evidence, repository, store, rules=None):
        self.rules=rules
        self.lookup,self.evidence,self.repository,self.store=lookup,evidence,repository,store
        self._queue=Queue();self._lock=RLock();self._thread=None;self._recovered=False

    def _ensure_recovered(self):
        # Every new job passes this barrier. Never recover again after success:
        # doing so could interrupt work owned by this process.
        if not self._recovered:
            with self._lock:
                if not self._recovered:
                    self.repository.recover()
                    self._recovered=True

    def start(self):
        try: self._ensure_recovered()
        except FileStoreError: pass
        self._thread=Thread(target=self._worker,name='report-runner',daemon=True);self._thread.start()

    def close(self):
        if self._thread:
            self._queue.put(None);self._thread.join();self._thread=None

    def submit(self, request):
        r=request_data(request);rid,op=str(uuid4()),str(uuid4())
        self._ensure_recovered()
        self.store.save_input(rid,r)
        self.repository.create(rid,op,r)
        self._queue.put((rid,False))
        return {'report_id':rid,'operation_id':op,'status':'queued'}

    def retry(self, rid):
        identity(rid)
        with self._lock:
            self._ensure_recovered()
            state=self.repository.get(rid)
            if state['status']=='succeeded': return {'report_id':rid,'operation_id':state['operation_id'],'status':'succeeded','reused':True}
            if state['status'] in ('queued','running'): raise FileStoreError('OPERATION_ALREADY_RUNNING','Report is already running')
            self.store.read(rid) # Only complete hash-verified artifacts are retryable.
            self.repository.advance(rid)
            self._queue.put((rid,True))
        return {'report_id':rid,'operation_id':state['operation_id'],'status':'running','reused':True}

    def _worker(self):
        while True:
            task=self._queue.get()
            try:
                if task is None: return
                self.run(*task)
            finally: self._queue.task_done()

    def rows(self,scope,mode='accepted'):
        component_scope=dict(scope,date_from=None,date_to=None)
        components=self.lookup.execute({'query_type':'bridge_components','scope':component_scope,'acceptance_mode':'accepted'})['records']
        if mode=='pending_review':
            extra=self.lookup.execute({'query_type':'bridge_components','scope':component_scope,'acceptance_mode':mode})['records']
            components=list({r['record_id']:r for r in components+extra}.values())
        if not components: return []
        actual=dict(scope,component_ids=[r['record_id'] for r in components])
        return self.lookup.execute({'query_type':'component_observations','scope':actual,'acceptance_mode':mode})['records']

    def collect(self,scope):
        allrows=self.rows(dict(scope,date_from=None,date_to=None))
        rows=self.rows(scope)
        pending=self.rows(dict(scope,date_from=None,date_to=None,review_mode='accepted'),'pending_review')
        docs=self.repository.documents(scope)
        catalog={};output=[];missing=[];warnings=['专业审核未执行；基础检查不代表工程结论已审核。','未执行自动冲突判定；不合并跨文档局部病害，不计算总面积。']
        if pending:missing.append(f'范围内 {len(pending)} 条 pending 观测未作为已接纳事实纳入。')
        unknown=sum(r.get('observed_at') is None for r in allrows)
        if scope['date_from'] or scope['date_to']: missing.append(f'日期过滤排除了 {unknown} 条未知日期观测。')
        if not rows: missing.append('无符合范围的已接纳观测。')
        if any(d['source_kind']=='synthetic' for d in docs):warnings.append('synthetic/演示来源，不是实际检测事实；多份演示资料不是多次真实检测。')
        for d in docs:
            self.evidence.document(d['document_id'])
            if d.get('conflict_ids'):warnings.append('来源 '+d['document_id']+' 已登记冲突：'+', '.join(d['conflict_ids']))
        def add(eid):
            if eid not in catalog:
                raw=self.evidence.read(eid);e=raw['evidence'];doc=raw['document']
                if doc['document_id'] not in {d['document_id'] for d in docs}:raise FileStoreError('GENERATION_VALIDATION_FAILED','Citation is outside permitted sources')
                catalog[eid]={'evidence_id':eid,'document_id':doc['document_id'],'filename':doc['filename'],'source_kind':doc['source_kind'],'page_no':e.get('page_no'),'target_field':e['target_field'],'quote':e['quote']}
            return catalog[eid]
        for r in rows:
            field_refs={}
            for eid in r['evidence_ids']:
                raw=self.evidence.read(eid)
                if not any(v['record_id']==r['record_id'] and v['acceptance_status']=='accepted' and v['source_acceptance_status']=='accepted' for v in raw['records']):raise FileStoreError('GENERATION_VALIDATION_FAILED','Evidence is not attached to this accepted record')
                item=add(eid);field_refs.setdefault(item['target_field'],[]).append(eid)
            values={f:deepcopy(r.get(f)) for f in FIELDS}
            for f,v in values.items():
                target=('area_expression' if values.get('area_expression') else 'area_original_value') if f=='area_value' else ('area_expression' if values.get('area_expression') else 'area_original_unit') if f=='area_unit' else f
                if v is not None and f not in METADATA and not field_refs.get(target):raise FileStoreError('GENERATION_VALIDATION_FAILED','Missing field evidence: '+f)
            if values['area_value'] is not None:
                if values.get('area_expression') and not any(values['area_expression'] in catalog[eid]['quote']
                        for eid in field_refs.get('area_expression',[])):
                    raise FileStoreError('GENERATION_VALIDATION_FAILED','Derived area expression is absent from citations')
                if not record_area_valid(values,self.rules):raise FileStoreError('GENERATION_VALIDATION_FAILED','Unsupported or inconsistent area')
            if values['observed_at'] is None:missing.append(r['record_id']+'：观测日期未知。')
            if values['area_value'] is None:missing.append(r['record_id']+'：面积未知。')
            output.append({'row_id':r['record_id'],'component_id':r['component_id'],'defect_id':r['defect_id'],'observation_id':r['record_id'],'record_ids':[r['component_id'],r['defect_id'],r['record_id']],'evidence_ids':sorted(r['evidence_ids']),'field_evidence':field_refs,'values':values})
        statements=[]
        if scope['date_from'] or scope['date_to'] or scope['review_mode']=='confirmed_only':warnings.append('未纳入日期未知或未经人工确认的原文说明。')
        else:
            for item in self.repository.statements(scope,[d['document_id'] for d in docs]):
                e=add(item['evidence_id'])
                statements.append({'evidence_id':e['evidence_id'],'quote':e['quote'],'kind':item['kind']})
            if not statements:warnings.append('未找到已登记的可引用原文说明；未新建索引或证据。')
        return rows,docs,output,statements,catalog,missing,warnings

    def build(self,rid,scope):
        rows,docs,output,statements,catalog,missing,warnings=self.collect(scope)
        payload=[[],[{'document_id':d['document_id'],'filename':d['original_filename'],'source_kind':d['source_kind'],'processing_status':d['processing_status']} for d in docs],output,statements,list(catalog.values())]
        report={'schema_version':'1.0','report_id':rid,'template_id':TEMPLATE,'template_version':'1.2','generated_at':datetime.now(timezone.utc).isoformat(),'scope':scope,'source_document_ids':[d['document_id'] for d in docs],'sections':[{'section_id':sid,'title':title,'claims':[],'rows':body} for (sid,title),body in zip(SECTIONS,payload)],'missing_information':missing,'conflicts':[],'warnings':warnings,'checks':{k:'passed' for k in ('schema','references','assertions','artifacts')},'professional_review':'not_run'}
        return report

    def recheck(self,report):
        current=self.build(report['report_id'],report['scope'])
        for key in ('scope','source_document_ids','sections','missing_information','conflicts','warnings'):
            if current[key]!=report[key]:raise FileStoreError('GENERATION_VALIDATION_FAILED','Report source state changed; create a new report')
        return self.rows(report['scope'])

    def run(self,rid,retry=False):
        with self._lock:
            try:
                state=self.repository.get(rid)
                if state['status']=='succeeded':return
                if not retry:self.repository.advance(rid)
                if retry:report,markdown,manifest=self.store.read(rid)
                else:
                    report=self.build(rid,state['request']['scope'])
                    self.store.validate(report)
                    self.recheck(report)
                    manifest=self.store.save(report,render(report))
                    report,markdown,manifest=self.store.read(rid)
                snapshots=self.recheck(report)
                self.repository.commit(report,snapshots,manifest)
            except Exception as error:
                code=error.code if isinstance(error,FileStoreError) else 'REPORT_FAILED'
                try:self.repository.fail(rid,code)
                except FileStoreError:pass # A graph outage leaves queued/running; restart recovery exposes interrupted.

    def get(self,rid,markdown=False):
        identity(rid);self._ensure_recovered();state=self.repository.get(rid)
        result={k:v for k,v in state.items() if k!='request'}
        if state['status']!='succeeded':
            if markdown:raise FileStoreError('REPORT_NOT_PUBLISHED','Report has not been registered')
            return result
        report,md,manifest=self.store.read(rid)
        if state.get('registration') is not None and json.loads(state['registration']['manifest_json'])!=manifest:raise FileStoreError('ARTIFACT_MISMATCH','Report differs from registered hashes')
        for doc in report['source_document_ids']:self.evidence.document(doc)
        changes=[]
        for row in report['sections'][2]['rows']:
            current=self.lookup.execute({'query_type':'record_by_id','record_id':row['observation_id'],'scope':report['scope']})['records']
            if not current or {f:current[0].get(f) for f in FIELDS}!=row['values'] or sorted(current[0]['evidence_ids'])!=row['evidence_ids']:changes.append(row['observation_id'])
        current_statements={(v['evidence_id'],v['kind']) for v in self.repository.statements(report['scope'],report['source_document_ids'])}
        for row in report['sections'][3]['rows']:
            if (row['evidence_id'],row['kind']) not in current_statements:changes.append(row['evidence_id'])
        for row in report['sections'][4]['rows']:
            raw=self.evidence.read(row['evidence_id'])
            if raw['evidence']['quote']!=row['quote']:changes.append(row['evidence_id'])
        result.update(report=report,citation_status_changes=changes)
        if markdown:return md,changes
        return result
