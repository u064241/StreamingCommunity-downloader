const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function create() {
  const elements = new Map(), requests = [], changes = [], storage = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {value:'', textContent:'', hidden:false, style:{}});
    return elements.get(id);
  };
  let domain = 'streamingcommunityz.dad';
  const context = vm.createContext({
    console, Promise, Map, setTimeout, clearTimeout,
    window:{addEventListener(){}, t:key=>key},
    document:{addEventListener(){}, getElementById:element},
    sessionStorage:{getItem:key=>storage.get(key)||null, setItem:(key,value)=>storage.set(key,value)},
    registerActions(){}, registerPageHash(){}, parseHash:()=>({page:'search'}),
    api:{get:async path=>{
      requests.push(path);
      return path === '/api/domain' ? {domain, version:'new-version', valid:true}
        : {current:domain, candidate:null, status:'applied'};
    }},
    _routePage:'search', _homeCache:{'streamingcommunity:old':{}, 'all:old':{}},
    _searchAbort:{abort:()=>changes.push('abort')},
    _cancelCatalogWork:()=>changes.push('cancel'),
    doSearch:async()=>changes.push('search'), showStartPage:async()=>changes.push('home'),
    loadTitlePage:async route=>{await vm.runInContext('ensureDomain()',context); changes.push(route);},
    _tp:null, _tpToken:0,
  });
  const run = code=>vm.runInContext(code,context);
  run(fs.readFileSync('app/static/app.js','utf8'));
  run("currentDomain='streamingcommunityz.nexus'; currentVersion='old-version'");
  return {context, run, element, requests, changes, storage, setDomain:value=>{domain=value;}};
}

test('applied domain synchronizes globals, badge, caches and the current search without restart',async()=>{
  const app=create();
  app.element('search-input').value='a retained query';
  app.storage.set('sc.search.return',JSON.stringify({hash:'#/search?q=test',y:400,page:2,
    results:[{source:'streamingcommunity',id:1}]}));
  await app.run('loadDomainStatus()');
  assert.equal(app.run('currentDomain'),'streamingcommunityz.dad');
  assert.equal(app.run('currentVersion'),'new-version');
  assert.equal(app.element('domain-badge').textContent,'streamingcommunityz.dad');
  assert.deepEqual(Object.keys(app.context._homeCache),[]);
  assert.deepEqual(app.changes,['abort','cancel','search']);
  assert.equal(app.element('search-input').value,'a retained query');
  const saved=JSON.parse(app.storage.get('sc.search.return'));
  assert.equal(saved.y,400);
  assert.equal(saved.hash,'#/search?q=test');
  assert.equal(saved.results,undefined);
});

test('candidate polling catches an applied change even when its SSE event was missed',async()=>{
  const app=create();
  await app.run('loadDomainCandidate()');
  assert.equal(app.run('currentDomain'),'streamingcommunityz.dad');
  assert.equal(app.changes.at(-1),'home');
  assert.equal(app.requests.filter(p=>p==='/api/domain').length,1);
});

test('simultaneous status refreshes share one request and unchanged domains do not reload content',async()=>{
  const app=create();
  app.run("currentDomain='streamingcommunityz.dad'");
  await Promise.all([app.run('loadDomainStatus()'),app.run('loadDomainStatus()'),app.run('loadDomainStatus()')]);
  assert.equal(app.requests.filter(p=>p==='/api/domain').length,1);
  assert.deepEqual(app.changes,[]);
});

test('first domain load does not duplicate the search already waiting on ensureDomain',async()=>{
  const app=create();
  app.run("currentDomain=''");
  await app.run('ensureDomain()');
  assert.equal(app.run('currentDomain'),'streamingcommunityz.dad');
  assert.deepEqual(app.changes,[]);
});

test('a StreamingCommunity title reloads on the new source and retains its season without self-deadlock',
  {timeout:2000},async()=>{
    const app=create();
    app.context._routePage='detail';
    app.context._tp={source:'streamingcommunity',type:'tv',id:42,slug:'serie',season:6};
    await app.run('loadDomainStatus()');
    const loaded=app.changes.at(-1);
    assert.equal(loaded.id,42);
    assert.equal(loaded.season,6);
    assert.equal(app.context._tpToken,1);
  });

test('global SSE domain_changed signal reloads authoritative server configuration',async()=>{
  const app=create();
  let stream;
  app.context.EventSource=class {constructor(){stream=this;} close(){}};
  app.run(fs.readFileSync('app/static/page-downloads.js','utf8'));
  app.run('connectGlobalStream()');
  stream.onmessage({data:JSON.stringify({type:'domain_changed',domain:'an-untrusted-payload.test'})});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(app.run('currentDomain'),'streamingcommunityz.dad');
  assert.ok(app.requests.includes('/api/domain'));
});
