"""Bounded cited answers: model selections, verified assertions, controlled text."""
from decimal import Decimal, InvalidOperation
import json
from uuid import uuid4
from ontology_proj.application.files import FileStoreError
from ontology_proj.application.semantic import search_request
from ontology_proj.domain.business import compact, UNITS
from ontology_proj.domain.measurement import record_area_valid

FIELDS={'name':'名称','bridge_code':'桥梁编号','component_code':'构件编号','component_type':'构件类型',
        'defect_code':'病害编号','defect_type':'病害类型','location_text':'位置','observation_code':'观测编号',
        'description':'描述','observed_at':'观测日期','area_value':'面积'}
REASONS={'NO_EVIDENCE':'当前范围没有可用证据。','MISSING_DATE':'资料未提供实际观测日期，不能采用报告编制日期。',
         'MISSING_AREA':'资料未提供可用的接纳面积。','PROFESSIONAL_NOT_SUPPORTED':'当前资料与系统能力不足以给出专业评级或处治方案。',
         'INSUFFICIENT_CONTEXT':'现有上下文不足，请补充资料或缩小问题范围。','SELECT_OBJECT':'请明确桥梁或构件范围。'}

def reject(message='Answer proposal failed verification'):
    raise FileStoreError('ANSWER_OUTPUT_INVALID',message)

def prepare_request(body):
    if not isinstance(body,dict) or set(body)-{'question','scope','top_k','graph_query'}:
        raise FileStoreError('INVALID_REQUEST','Unknown answer fields')
    query={'query':body.get('question'),'scope':body.get('scope'),'top_k':body.get('top_k',5)}
    _,scope,_=search_request(query)
    if 'graph_query' in body:query['graph_query']=body['graph_query']
    return query,scope


def equal_value(field,value,actual):
    if actual is None or type(value) is not type(actual):return False
    if field=='area_value':
        try:return Decimal(value).is_finite() and Decimal(value)==Decimal(actual)
        except (InvalidOperation,ValueError,TypeError):return False
    return value==actual


class AnswerQuestion:
    def __init__(self,context,lookup,evidence,generator,journal,rules=None):
        self.context,self.lookup,self.evidence,self.generator,self.journal=context,lookup,evidence,generator,journal
        self.rules=rules

    def prepare(self,body):
        request,scope=prepare_request(body);bundle=self.context.execute(request)
        records={};catalog={};statements=[];warnings=list(bundle['warnings']);used=0
        def add_record(record):
            nonlocal used
            rid=record['record_id']
            if rid in records:return
            if record['acceptance_status']!='accepted' or record['source_acceptance_status']!='accepted':return
            fields={f:record.get(f) for f in FIELDS if f in record}
            info={k:record[k] for k in ('record_id','record_type','source_document_ids','source_kinds','review_status')}
            info['fields']=fields
            info['qualifiers']={k:record.get(k) for k in ('assertion','scope','area_is_approximate','area_original_value','area_original_unit','area_unit','area_conversion','area_expression','area_basis','area_derivation','area_rules_version') if k in record}
            items={}
            # One current, independently verified citation per supported field.
            targets={}
            for eid in record['evidence_ids']:
                raw=self.evidence.read(eid);e=raw['evidence'];target=e['target_field']
                if target not in (*FIELDS,'area_original_value','area_original_unit','area_expression'):continue
                if target in targets:continue
                if not any(r['record_id']==rid and r['acceptance_status']=='accepted' and r['source_acceptance_status']=='accepted' and (scope['review_mode']!='confirmed_only' or r['review_status']=='confirmed') for r in raw['records']):continue
                targets[target]=eid
                items[eid]={'evidence_id':eid,'quote':e['quote'],'document_id':e['document_id'],'page_no':e['page_no'],
                            'kind':'fact','target_field':target,'record_ids':[rid],'source_kind':raw['document']['source_kind']}
            info['evidence_ids']=list(items)
            size=len(json.dumps([info,items],ensure_ascii=False))
            if len(records)>=10 or used+size>24000:
                warnings.append('Answer input budget excluded complete record');return
            used+=size;records[rid]=info
            for eid,item in items.items():
                if eid in catalog:catalog[eid]['record_ids']=sorted(set(catalog[eid]['record_ids']+[rid]))
                else:catalog[eid]=item
        for r in bundle['facts']:add_record(r)
        for c in bundle['source_statements']:
            items={}
            for s in c['source_spans']:
                raw=self.evidence.read(s['evidence_id']);e=raw['evidence']
                items[e['evidence_id']]={'evidence_id':e['evidence_id'],'quote':e['quote'],'document_id':e['document_id'],
                    'page_no':e['page_no'],'kind':'source_statement','record_ids':[], 'target_field':'chunk_source',
                    'semantic_kind':c['semantic_kind'],'statement_kind':c.get('statement_kind','other'),'source_kind':c['source_kind']}
            size=len(json.dumps(items,ensure_ascii=False))
            if used+size>24000:warnings.append('Answer input budget excluded complete statement');continue
            used+=size;catalog.update(items);statements.extend(items)
        for c in bundle['business_passages']:
            for rid in c['record_ids']:
                if rid in records or len(records)>=10:continue
                result=self.lookup.execute({'query_type':'record_by_id','record_id':rid,'scope':scope,'acceptance_mode':'accepted'})
                for r in result['records']:add_record(r)
        return {'status':bundle['status'],'scope':scope,'records':records,'evidence':catalog,'source_statement_ids':statements,
                'warnings':warnings,'retrieval':{'graph_ids':list(records),'chunk_ids':[c['chunk_id'] for c in bundle['business_passages']+bundle['source_statements']], 'semantic_status':bundle['status']}}

    def validate(self,draft,context):
        if not isinstance(draft,dict) or set(draft)!={'status','claims','reason_codes'}:reject('Draft fields are invalid')
        status=draft['status'];claims=draft['claims'];reasons=draft['reason_codes']
        if status not in ('answered','insufficient_evidence','needs_clarification') or not isinstance(claims,list) or len(claims)>20 or not isinstance(reasons,list) or len(reasons)>6 or any(not isinstance(r,str) or r not in REASONS for r in reasons):reject()
        if status!='answered':
            if claims or not reasons:reject('Non-answer must have reasons and no claims')
            return []
        if not claims or reasons:reject('Answered requires claims and no refusal reasons')
        result=[]
        for i,c in enumerate(claims):
            if not isinstance(c,dict) or set(c)!={'kind','record_ids','evidence_ids','assertions'}:reject('Claim fields are invalid')
            kind=c['kind'];rids=c['record_ids'];eids=c['evidence_ids'];assertions=c['assertions']
            if kind not in ('fact','source_statement','summary') or any(not isinstance(v,list) for v in (rids,eids,assertions)) or not 1<=len(eids)<=20 or len(rids)>10 or len(assertions)>20:reject()
            if any(not isinstance(e,str) or e not in context['evidence'] for e in eids) or any(not isinstance(r,str) or r not in context['records'] for r in rids):reject('Reference not in allowed context')
            refs=[context['evidence'][e] for e in eids]
            if kind=='source_statement':
                if rids or assertions or any(e['kind']!='source_statement' for e in refs):reject('Source statement cannot become a fact')
                text='资料原文：'+'；'.join(dict.fromkeys(e['quote'] for e in refs))
                statement_kind='other'
            else:
                if not assertions or not rids or any(e['kind']!='fact' for e in refs):reject('Fact needs record fields and paired evidence')
                rendered=[];canonical=[];asserted=[]
                for a in assertions:
                    if not isinstance(a,dict) or set(a)!={'record_id','field','value','unit'}:reject()
                    rid=a['record_id'];field=a['field']
                    if not isinstance(rid,str) or rid not in rids or not isinstance(field,str) or field not in FIELDS:reject()
                    record=context['records'][rid];value=record['fields'].get(field)
                    display=next((record['fields'][key] for key in ('observation_code','component_code','defect_code','bridge_code','name') if record['fields'].get(key)),rid)
                    if not equal_value(field,a['value'],value):reject('Assertion differs from graph value or is unknown')
                    derived=record['qualifiers'].get('area_expression') is not None
                    target=('area_expression' if derived else 'area_original_value') if field=='area_value' else field
                    if not any(rid in e['record_ids'] and e['target_field']==target for e in refs):reject('Citation does not support this record field')
                    unit='m²' if field=='area_value' else None
                    if a['unit']!=unit:reject('Assertion unit differs')
                    if field=='area_value':
                        if derived and not any(rid in e['record_ids'] and e['target_field']=='area_expression'
                                and record['qualifiers']['area_expression'] in e.get('quote','') for e in refs):
                            reject('Derived area requires its complete original expression')
                        if not derived and not any(rid in e['record_ids'] and e['target_field']=='area_original_unit' for e in refs):reject('Area requires paired unit evidence')
                        q=record['qualifiers']
                        if not record_area_valid(dict(q,area_value=value),self.rules):reject('Unsupported or inconsistent area conversion')
                        qualifier='（近似）' if q.get('area_is_approximate') is True else '（近似性未知）' if q.get('area_is_approximate') is None else '（非近似）'
                        assertion={'positive':'肯定观测','negative':'否定观测','suspected':'疑似观测'}.get(q.get('assertion'),str(q.get('assertion')))
                        scope={'single_defect':'单处病害','multiple_defects':'多处病害','component_total':'构件汇总','bridge_total':'全桥汇总'}.get(q.get('scope'),str(q.get('scope')))
                        basis='（尺寸推算）' if q.get('area_basis')=='rectangle_dimensions' else '（原文面积式计算）' if q.get('area_basis')=='explicit_area_product' else ''
                        rendered.append(display+'：面积 '+value+' m²'+qualifier+basis+'；'+assertion+'，范围：'+scope)
                    else:rendered.append(display+'：'+FIELDS[field]+'='+str(value))
                    canonical.append({'record_id':rid,'field':field,'value':value,'unit':unit});asserted.append(rid)
                if set(asserted)!=set(rids) or any(not set(e['record_ids']).intersection(rids) for e in refs):reject('Claim records and evidence are not paired')
                c=dict(c,assertions=canonical);text='；'.join(rendered);statement_kind=None
            item=dict(c,claim_id='C'+str(i+1),text=text)
            if statement_kind:item['statement_kind']=statement_kind;item['semantic_kinds']=sorted({e['semantic_kind'] for e in refs})
            result.append(item)
        return result

    def recheck(self,claims,context):
        # Re-read allowed IDs and quoted text after the model request.
        checked=set()
        for c in claims:
            for eid in c['evidence_ids']:
                raw=self.evidence.read(eid);expected=context['evidence'][eid]
                if raw['evidence']['quote']!=expected['quote'] or raw['evidence']['document_id']!=expected['document_id']:reject('Evidence changed during generation')
                if expected['kind']=='fact' and any(not any(r['record_id']==rid and r['acceptance_status']=='accepted' and r['source_acceptance_status']=='accepted' and (context['scope']['review_mode']!='confirmed_only' or r['review_status']=='confirmed') for r in raw['records']) for rid in set(c['record_ids']).intersection(expected['record_ids'])):reject('Record acceptance changed during generation')
            for rid in c['record_ids']:
                if rid in checked:continue
                checked.add(rid)
                rows=self.lookup.execute({'query_type':'record_by_id','record_id':rid,'scope':context['scope'],'acceptance_mode':'accepted'})['records']
                if len(rows)!=1:reject('Record no longer in allowed scope')
                for a in [a for claim in claims for a in claim['assertions']]:
                    if a['record_id']==rid and not equal_value(a['field'],a['value'],rows[0].get(a['field'])):reject('Record changed during generation')
                if any(rows[0].get(k)!=v for k,v in context['records'][rid]['qualifiers'].items()):reject('Record qualifiers changed during generation')

    def execute(self,body):
        prepare_request(body);qid=str(uuid4())
        try:
            self.journal.save(qid,'input',body)
            return self._execute(body,qid)
        except FileStoreError as exc:
            if not hasattr(exc,'question_id'):
                exc.question_id=qid
                self.journal.save(qid,'validation',{'passed':False,'error_code':exc.code,'question_id':qid})
            raise

    def _execute(self,body,qid):
        context=self.prepare(body)
        self.journal.save(qid,'context',context)
        warnings=list(context['warnings'])
        if any(e['source_kind']=='synthetic' for e in context['evidence'].values()):warnings.append('来源为 synthetic 演示素材，不是实际检测事实。')
        if any(r['review_status']!='confirmed' for r in context['records'].values()) or context['source_statement_ids']:warnings.append('所用资料未全部经过人工专业复核。')
        try:
            if context['status']=='needs_clarification':draft={'status':'needs_clarification','claims':[],'reason_codes':['SELECT_OBJECT']}
            elif not context['records'] and not context['source_statement_ids']:draft={'status':'insufficient_evidence','claims':[],'reason_codes':['NO_EVIDENCE']}
            else:
                if self.generator is None:raise FileStoreError('MODEL_NOT_CONFIGURED','Answer model is not configured')
                request=self.generator.request(body['question'],context);self.journal.save(qid,'request',request)
                response=self.generator.generate(request);self.journal.save(qid,'response',response)
                if response.get('error_code') or response.get('diagnostics'):
                    raise FileStoreError(response.get('error_code') or 'MODEL_OUTPUT_INVALID','Answer generation request failed')
                draft=response.get('candidate')
            claims=self.validate(draft,context);self.recheck(claims,context)
            answer={'schema_version':'1.0','question_id':qid,'status':draft['status'],'scope':context['scope'],'claims':claims,
                'missing_information':[REASONS[r] for r in draft['reason_codes']],'reason_codes':draft['reason_codes'],'conflicts':[],
                'warnings':list(dict.fromkeys(warnings)),'retrieval':context['retrieval']}
            self.journal.save(qid,'validation',{'passed':True});self.journal.save(qid,'answer',answer)
            return answer
        except FileStoreError as exc:
            self.journal.save(qid,'validation',{'passed':False,'error_code':exc.code,'question_id':qid})
            exc.question_id=qid
            raise
