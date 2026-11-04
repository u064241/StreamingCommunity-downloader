const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync('app/static/portal-lifecycle.js','utf8');
function create(beaconResult){
  const listeners={},documentEvents={},calls=[],beacons=[],timers=new Map();let seq=0;
  const context=vm.createContext({
    document:{currentScript:{dataset:{token:'portal-token'}},addEventListener:(event,fn)=>{documentEvents[event]=fn;}},
    window:{addEventListener:(event,fn)=>{listeners[event]=fn;}},
    navigator:{sendBeacon:(path,body)=>{beacons.push([path,JSON.parse(body)]);return beaconResult;}},
    crypto:{randomUUID:()=>`document-${++seq}`},
    fetch:(path,options)=>{calls.push([path,JSON.parse(options.body)]);return Promise.resolve({});},
    setInterval:fn=>{const id=++seq;timers.set(id,fn);return id;},clearInterval:id=>timers.delete(id),
  });
  vm.runInContext(source,context);
  return {listeners,documentEvents,calls,beacons,timers};
}
test('closing stops heartbeats and falls back when the beacon is refused',()=>{
  const app=create(false);
  const pendingHeartbeat=[...app.timers.values()][0];
  app.listeners.pagehide();
  pendingHeartbeat();
  app.documentEvents.visibilitychange();
  app.listeners.pagehide();
  assert.equal(app.beacons.length,1);
  assert.deepEqual(app.calls.map(row=>row[0]),['/api/portal/heartbeat','/api/portal/close']);
  assert.equal(app.timers.size,0);
});
test('restored pages use a new identity and a new heartbeat timer',()=>{
  const app=create(true);
  const first=app.calls[0][1].client_id;
  app.listeners.pagehide();
  app.listeners.pageshow();
  assert.notEqual(app.calls.at(-1)[1].client_id,first);
  assert.equal(app.timers.size,1);
  assert.equal(app.calls.filter(row=>row[0]==='/api/portal/close').length,0);
});
