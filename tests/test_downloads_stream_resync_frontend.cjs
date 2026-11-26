// The Downloads page resyncs when the stream names a job it has no card for
// (its job_created was dropped by the server's drop-oldest fan-out).
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function create(serverJobs) {
  const requests = [], renders = [];
  let source = null;
  class FakeEventSource {
    constructor(url) { this.url = url; source = this; }
    close() {}
  }
  const el = {textContent:'', style:{}};
  const context = vm.createContext({
    console, Promise, Map, Set, setTimeout, clearTimeout,
    window:{addEventListener(){}, t:key=>key},
    document:{getElementById:()=>null, querySelector:()=>el},
    EventSource:FakeEventSource,
    api:{get:async path=>{requests.push(path); return serverJobs;}},
    registerActions(){}, registerPageHash(){},
  });
  const run = code=>vm.runInContext(code, context);
  run('var _jobs = new Map(); var _jobPhases = {};');
  run(fs.readFileSync('app/static/page-downloads.js', 'utf8'));
  context.__renders = renders;
  run('renderAllJobCards = () => __renders.push([..._jobs.keys()]); updateActiveBadge = () => {};');
  run('connectGlobalStream()');
  const send = msg => source.onmessage({data:JSON.stringify(msg)});
  return {run, requests, renders, send};
}

test('a progress frame for an unknown job re-reads /api/jobs once and adds the card', async()=>{
  const app = create([{job_id:'old', status:'done'}, {job_id:'new', status:'running'}]);
  app.send({type:'snapshot', jobs:[{job_id:'old', status:'done'}]});
  app.send({type:'progress', job_id:'new', phase:'video', pct:12.5, current:5, total:40});
  await app.run('_resyncPending');
  assert.deepEqual(app.requests, ['/api/jobs']);
  assert.equal(app.run("_jobs.has('new')"), true);
  assert.equal(JSON.stringify(app.renders.at(-1)), JSON.stringify(['old', 'new']));

  // Known now: the next frame updates it, no further request.
  app.send({type:'progress', job_id:'new', phase:'video', pct:20, current:8, total:40});
  assert.equal(app.run("_jobs.get('new').progress.pct"), 20);
  assert.deepEqual(app.requests, ['/api/jobs']);
});

test('a job the server no longer lists is tried once, not on every frame', async()=>{
  const app = create([]);
  app.send({type:'snapshot', jobs:[]});
  app.send({type:'status', job_id:'gone', phase:'converting'});
  await app.run('_resyncPending');
  app.send({type:'progress', job_id:'gone', phase:'converting', pct:50});
  app.send({type:'job_status', job_id:'gone', status:'running'});
  await app.run('_resyncPending');
  assert.deepEqual(app.requests, ['/api/jobs']);
});

test('known jobs never trigger a resync', async()=>{
  const app = create([]);
  app.send({type:'snapshot', jobs:[{job_id:'a', status:'running'}]});
  app.send({type:'progress', job_id:'a', phase:'video', pct:1});
  app.send({type:'status', job_id:'a', phase:'converting'});
  await app.run('_resyncPending');
  assert.deepEqual(app.requests, []);
});
