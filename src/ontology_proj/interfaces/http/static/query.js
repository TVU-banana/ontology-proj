import * as pdfjs from './vendor/pdfjs/build/pdf.mjs';
pdfjs.GlobalWorkerOptions.workerSrc='/static/vendor/pdfjs/build/pdf.worker.mjs';
const labels={accepted:'已接纳',pending_review:'待复核',unreviewed:'未人工复核',confirmed:'已人工确认',rejected:'已驳回'};const label=s=>labels[s]||s||'未知';
const $=id=>document.getElementById(id), list=s=>s.split(',').map(v=>v.trim()).filter(Boolean);
let evidence=null,pdf=null,scale=1,rotation=0,sequence=0,chain=Promise.resolve(),querySequence=0;
const status=(id,text,error=false)=>{$(id).textContent=text;$(id).classList.toggle('error',error);};
async function api(url,options){const response=await fetch(url,options);const body=await response.json();if(!response.ok)throw new Error(`${body.error?.code || response.status}：${body.error?.message || '请求失败'}`);return body;}
function text(tag,value,cls){const el=document.createElement(tag);el.textContent=value;if(cls)el.className=cls;return el;}
$('theme').onchange=()=>{document.documentElement.dataset.theme=$('theme').value;};
$('documents').value=new URLSearchParams(location.search).get('document_id')||'';
const params=new URLSearchParams(location.search);
$('bridge').value=params.get('bridge_id')||'';
$('components').value=params.get('component_ids')||'';
$('date-from').value=params.get('date_from')||'';
$('date-to').value=params.get('date_to')||'';
$('synthetic').checked=params.get('include_synthetic')!=='false';
$('confirmed').checked=params.get('review_mode')==='confirmed_only';
if($('components').value)$('type').value='component_observations';
$('type').onchange=()=>{$('identity-label').hidden=!['defect_history','record_by_id'].includes($('type').value);};
$('query-form').onsubmit=async event=>{
 event.preventDefault();++sequence;$('highlight').hidden=true;$('page').hidden=true;$('controls').hidden=true;$('evidence-meta').replaceChildren();$('quote').replaceChildren();status('evidence-status','');enqueue(async()=>{const previous=pdf;pdf=null;evidence=null;if(previous)await previous.loadingTask.destroy();});const token=++querySequence;$('submit').disabled=true;$('results').replaceChildren();status('query-status','正在查询…');
 const type=$('type').value,request={query_type:type,scope:{document_ids:list($('documents').value),bridge_id:$('bridge').value.trim()||null,component_ids:list($('components').value),date_from:$('date-from').value||null,date_to:$('date-to').value||null,review_mode:$('confirmed').checked?'confirmed_only':'accepted',include_synthetic:$('synthetic').checked},acceptance_mode:$('acceptance').value,area_min:$('area-min').value.trim()||null,area_max:$('area-max').value.trim()||null};
 if(type==='record_by_id')request.record_id=$('identity').value.trim();if(type==='defect_history')request.defect_id=$('identity').value.trim();
 try{const data=await api('/api/graph/lookup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)});if(token!==querySequence)return;
 status('query-status',`${data.count} 条记录。${data.warnings.map(w=>w==='Pending records are for inspection, not accepted business facts'?'待复核记录仅供查看缺项，尚未作为业务事实接纳。':w).join('；')}`);
 for(const r of data.records){const item=document.createElement('article');item.append(text('h3',r.name||r.description||r.observation_code||r.component_code||r.record_type));
 item.append(text('p',`来源：${r.source_kinds.includes('synthetic')?'SYNTHETIC / 演示':'真实'} · 接纳：${label(r.source_acceptance_status||r.acceptance_status)} · 人工复核：${label(r.review_status)}`));
 if(r.record_type==='Observation')item.append(text('p',`面积：${r.area_value===null?'未知':r.area_value+' m²'+(r.area_is_approximate?'（约）':r.area_is_approximate===null?'（近似性未知）':'')+(r.area_basis==='rectangle_dimensions'?'（尺寸推算）':r.area_basis==='explicit_area_product'?'（原文面积式计算）':'')} · 日期：${r.observed_at||'未知'}`));
 const refs=text('div','','references');r.evidence_ids.forEach((id,i)=>{const b=text('button',`引用 ${i+1}`);b.type='button';b.onclick=()=>openEvidence(id);refs.append(b);});item.append(refs);$('results').append(item);}
 }catch(error){if(token===querySequence)status('query-status',error.message,true);}finally{if(token===querySequence)$('submit').disabled=false;}
};
function enqueue(fn){chain=chain.catch(()=>{}).then(fn).catch(error=>{status('evidence-status',error.message,true);$('highlight').hidden=true;$('page').hidden=true;});return chain;}
async function openEvidence(id){const token=++sequence;status('evidence-status','正在校验证据与原文件…');$('highlight').hidden=true;$('page').hidden=true;$('quote').replaceChildren();$('evidence-meta').replaceChildren();$('controls').hidden=true;
 return enqueue(async()=>{if(token!==sequence)return;const previous=pdf;pdf=null;evidence=null;if(previous)await previous.loadingTask.destroy();const data=await api('/api/evidence/'+encodeURIComponent(id));if(token!==sequence)return;evidence=data;$('evidence-panel').scrollIntoView({behavior:'smooth',block:'start'});
 $('evidence-meta').append(text('p',`${data.document.filename} · ${data.document.source_kind==='synthetic'?'SYNTHETIC / 演示':'真实'} · 第 ${data.evidence.page_no??'未知'} 页`),text('p',data.records.map(r=>`${r.record_type}：${label(r.source_acceptance_status)} / ${label(r.review_status)}`).join('；')));
 const chars=Array.from(data.block_text),[start,end]=data.evidence.charspan;
 $('quote').append(document.createTextNode(chars.slice(0,start).join('')),text('mark',chars.slice(start,end).join('')),document.createTextNode(chars.slice(end).join('')));
 $('pdf-link').href=data.pdf_url;scale=1;rotation=0;
 if(data.evidence.page_no===null){status('evidence-status',data.warnings.join('；'));return;}
 pdf=await pdfjs.getDocument({url:data.pdf_url,cMapUrl:'/static/vendor/pdfjs/cmaps/',cMapPacked:true,standardFontDataUrl:'/static/vendor/pdfjs/standard_fonts/',wasmUrl:'/static/vendor/pdfjs/wasm/'}).promise;
 if(token!==sequence)return;$('controls').hidden=false;await render(token);
 });
}
async function render(token){if(!pdf||token!==sequence)return;const page=await pdf.getPage(evidence.evidence.page_no);const viewport=page.getViewport({scale,rotation:(page.rotate+rotation)%360});const canvas=$('canvas'),ratio=window.devicePixelRatio||1;
 canvas.width=Math.floor(viewport.width*ratio);canvas.height=Math.floor(viewport.height*ratio);canvas.style.width=viewport.width+'px';canvas.style.height=viewport.height+'px';$('page').hidden=false;
 await page.render({canvasContext:canvas.getContext('2d'),viewport,transform:ratio!==1?[ratio,0,0,ratio,0,0]:null}).promise;
 if(token!==sequence)return;$('zoom').value=Math.round(scale*100)+'%';$('highlight').hidden=true;
 const g=evidence.page_geometry,b=evidence.evidence.bbox;let warnings=[...evidence.warnings];
 if(b&&g&&g.rotation===0&&Math.abs(page.view[2]-page.view[0]-g.width)<1&&Math.abs(page.view[3]-page.view[1]-g.height)<1){
 const box=[...viewport.convertToViewportPoint(page.view[0]+b[0]*g.width,page.view[1]+(1-b[3])*g.height),...viewport.convertToViewportPoint(page.view[0]+b[2]*g.width,page.view[1]+(1-b[1])*g.height)];
 const h=$('highlight');h.style.left=Math.min(box[0],box[2])+'px';h.style.top=Math.min(box[1],box[3])+'px';h.style.width=Math.abs(box[2]-box[0])+'px';h.style.height=Math.abs(box[3]-box[1])+'px';h.hidden=false;
 }else if(b)warnings.push('PDF 几何与证据不兼容，未绘制区域');
 status('evidence-status',warnings.length?warnings.join('；'):'原文和页面已校验。黄色框标示引用区域。');
}
$('zoom-in').onclick=()=>{scale=Math.min(3,scale+.25);enqueue(()=>render(sequence));};$('zoom-out').onclick=()=>{scale=Math.max(.5,scale-.25);enqueue(()=>render(sequence));};$('rotate').onclick=()=>{rotation=(rotation+90)%360;enqueue(()=>render(sequence));};
window.addEventListener('resize',()=>enqueue(()=>render(sequence)));

// Stable report citations open the same verified evidence panel.
if(location.pathname.startsWith('/evidence/')) openEvidence(decodeURIComponent(location.pathname.slice('/evidence/'.length)));
