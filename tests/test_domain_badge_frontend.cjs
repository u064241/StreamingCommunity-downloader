// The domain badge is green only when GET /api/domain says "ok": a paused or
// unverified source shows yellow with the age of the last successful check.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function create(answer) {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {value:'', textContent:'', className:'', title:'', hidden:false, style:{}});
    return elements.get(id);
  };
  const context = vm.createContext({
    console, Promise, Map, setTimeout, clearTimeout,
    window:{addEventListener(){}, t:(key, params)=>params ? `${key}:${JSON.stringify(params)}` : key},
    document:{addEventListener(){}, getElementById:element},
    sessionStorage:{getItem:()=>null, setItem(){}},
    registerActions(){}, registerPageHash(){}, parseHash:()=>({page:'search'}),
    openSettings(){},
    api:{get:async path=>path === '/api/domain' ? answer
      : {current:answer.domain, candidate:null, status:'healthy'}},
  });
  vm.runInContext(fs.readFileSync('app/static/app.js','utf8'), context);
  vm.runInContext("currentDomain='streamingcommunityz.dad'", context);
  return {run:code=>vm.runInContext(code, context), element};
}

test('a recently verified domain is green, as before', async()=>{
  const app = create({domain:'streamingcommunityz.dad', valid:true, version:'v', status:'ok', verified_age:5});
  await app.run('loadDomainStatus()');
  const badge = app.element('domain-badge');
  assert.equal(badge.className, 'badge bg-success');
  assert.equal(badge.textContent, 'streamingcommunityz.dad');
});

test('an answer without status (older server) still reads as ok', async()=>{
  const app = create({domain:'streamingcommunityz.dad', valid:true, version:'v'});
  await app.run('loadDomainStatus()');
  assert.equal(app.element('domain-badge').className, 'badge bg-success');
});

test('a paused source is yellow, not green, with the age of the last check', async()=>{
  const app = create({domain:'streamingcommunityz.dad', valid:true, version:'v', status:'paused', verified_age:3720});
  await app.run('loadDomainStatus()');
  const badge = app.element('domain-badge');
  assert.equal(badge.className, 'badge bg-warning text-dark');
  assert.equal(badge.textContent, 'streamingcommunityz.dad · domain.badge.paused (1 h)');
  assert.equal(badge.title, 'domain.badge.verifiedAgo:{"age":"1 h"}');
});

test('an unverified domain never checked is yellow and says so', async()=>{
  const app = create({domain:'streamingcommunityz.dad', valid:true, version:'', status:'unverified', verified_age:null});
  await app.run('loadDomainStatus()');
  const badge = app.element('domain-badge');
  assert.equal(badge.className, 'badge bg-warning text-dark');
  assert.equal(badge.textContent, 'streamingcommunityz.dad · domain.badge.unverified');
  assert.equal(badge.title, 'domain.badge.neverVerified');
});
