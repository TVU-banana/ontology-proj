// Functional DOM and fetch doubles; this is not a rendered browser test.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('src/ontology_proj/interfaces/http/static/workspace.html','utf8');
const old=fs.readFileSync('specs/012-bridge-identity/results/baseline/src/ontology_proj/interfaces/http/static/workspace.html','utf8');
const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
assert.equal(new Set(ids).size,ids.length);
for(const m of old.matchAll(/\bid="([^"]+)"/g))assert.ok(ids.includes(m[1]),'Missing old control '+m[1]);
class E {constructor(){this.value='';this.checked=false;this.disabled=false;this.children=[];this.classList={toggle(){}};}append(...a){this.children.push(...a);}replaceChildren(...a){this.children=a;}addEventListener(){}}
const elements=Object.fromEntries(ids.map(id=>[id,new E()]));
const calls=[],bridge={bridge_id:'brg_fixture',name:'测试桥',location:'测试市测试路',source_kind:'real'};
const document={getElementById(id){assert.ok(elements[id],'Unobserved DOM ID '+id);return elements[id];},createElement(){return new E();},documentElement:{dataset:{}}};
const context=vm.createContext({document,window:{addEventListener(){}},URLSearchParams,crypto:{randomUUID(){return 'fixture-key';}},setTimeout,clearTimeout,console,
fetch:async(url,options)=>{calls.push({url,body:options?.body?JSON.parse(options.body):undefined});const data=url==='/api/bridges'?(options?.method==='POST'?{bridge,duplicate:false}:{bridges:[bridge]}):{status:'resolved',bridge_id:bridge.bridge_id,reason:'user_selection'};return {ok:true,json:async()=>data};}});
(async()=>{
vm.runInContext(fs.readFileSync('src/ontology_proj/interfaces/http/static/workspace.js','utf8'),context);
await elements['bridges-refresh'].onclick();assert.equal(elements['bridge-options'].children[0].value,bridge.bridge_id);
elements['bridge-name'].value=bridge.name;elements['bridge-location'].value=bridge.location;elements['source-kind'].value='real';
elements['bridge-register-form'].onsubmit({preventDefault(){},submitter:new E()});await new Promise(setImmediate);
assert.equal(elements.bridge.value,bridge.bridge_id);assert.equal(calls.find(c=>c.body?.name).body.source_kind,'real');
elements.document.value='doc_fixture';await elements['bridge-resolution-confirm'].onclick();assert.equal(calls.at(-1).body.selected_bridge_id,bridge.bridge_id);assert.equal(calls.at(-1).body.confirm_details,true);
vm.runInContext("showBridgeResolution({status:'needs_confirmation',reason:'BRIDGE_OFFICIAL_CONFLICT'})",context);
assert.equal(elements['bridge-directory'].open,true);assert.ok(elements['bridge-resolution-status'].textContent.includes('官方编号冲突'));
assert.ok(elements['bridge-resolution-status'].textContent.includes('登记新桥'));
const result={passed:true,boundary:'HTML controls and JS handlers exercised with DOM/fetch doubles; no actual browser, API or network',old_controls_retained:[...old.matchAll(/\bid="([^"]+)"/g)].length,new_controls:ids.length,cases:['refresh/list options','register and populate shared ID','confirm selected report ownership','conflict reminder and expanded controls'],api_calls:calls};
const path='specs/012-bridge-identity/results/ui-functional.json';assert.ok(!fs.existsSync(path));fs.writeFileSync(path,JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify({passed:true,old_controls:result.old_controls_retained,cases:result.cases.length}));
})();
