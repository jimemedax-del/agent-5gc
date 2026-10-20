(async()=>{
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const html=fs.readFileSync(process.argv[2] || 'results/20261020-cnf-requirements-d2/review-01/review.html','utf8');
const packet=JSON.parse(html.match(/<script type="application\/json" id="data">([\s\S]*?)<\/script>/)[1]);
class Element{constructor(){this.children=[];this.value='';this.checked=false;this.textContent='';} append(...items){this.children.push(...items);} click(){}}
const nodes={};for(const id of ['data','definitions','items','reviewer','human','independent','export','status']) nodes[id]=new Element();
nodes.data.textContent=JSON.stringify(packet);let exported=[];
const context=vm.createContext({document:{getElementById:id=>nodes[id],createElement:()=>new Element()},
 Blob,URL:{createObjectURL:blob=>{exported.push(blob);return 'blob:unit-only';},revokeObjectURL:()=>{}},setTimeout:callback=>callback()});
vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],context);
const controls=vm.runInContext('controls',context);
assert.equal(controls.length,36);assert.ok(controls.every(c=>c.select.value===''));
nodes.export.onclick();assert.equal(exported.length,1);
const initial=JSON.parse(await exported[0].text());assert.ok(initial.items.every(i=>i.review.decision==='unreviewed'));
controls[0].select.value='streaming';controls[0].reason.value='Temporary engineering export test, not a human annotation';
nodes.reviewer.value='unit-fixture-reviewer';nodes.export.onclick();assert.equal(exported.length,1);
nodes.human.checked=true;nodes.independent.checked=true;nodes.export.onclick();assert.equal(exported.length,2);
const labeled=JSON.parse(await exported[1].text());assert.equal(labeled.items[0].review.category,'streaming');
assert.ok(labeled.items.slice(1).every(i=>i.review.decision==='unreviewed'));
assert.ok(labeled.items.every(i=>i.targetReview.status==='unreviewed'));
const raw=p=>p.items.map(i=>({id:i.id,input:i.input,origin:i.origin,scenarioFamily:i.scenarioFamily,split:i.split}));
assert.deepEqual(raw(labeled),raw(packet));
process.stdout.write(JSON.stringify({scope:'temporary DOM simulation only; no human labels or browser downloads saved',
 checks:['36 initially unreviewed','empty export remains unreviewed','missing human/independence declaration rejected',
 'selected category exported without inference','target reviews remain pending','immutable raw facts preserved'],passed:true})+'\n');

})().catch(error=>{console.error(error);process.exitCode=1;});
