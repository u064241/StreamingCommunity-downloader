const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');
const source = fs.readFileSync('app/static/panel.js', 'utf8');
function setup(api, confirm = async () => true) {
  const nodes = Object.fromEntries(['notif-badge','notif-list','notif-actions','notif-panel'].map(id => [id,{style:{display:'block'},innerHTML:'',textContent:''}]));
  const button = {disabled:false};
  const context = vm.createContext({api, scConfirm:confirm,
    document:{getElementById:id=>nodes[id], querySelectorAll:()=>[button], addEventListener:()=>{}},
    registerActions:()=>{}, showToast:()=>{}, errText:e=>e.message,
    NOTIFICATION_ICONS:{}, escapeHtml:v=>v, fmtDate:v=>v,
  });
  vm.runInContext(source, context);
  return {context,nodes,button};
}
test('a stale refresh cannot restore deleted notifications', async () => {
  let resolveOld;
  let reads = 0;
  const api = {
    get: async()=> ++reads === 1 ? new Promise(resolve=>{resolveOld=resolve;}) : {unread:0,items:[]},
    post: async()=>({deleted:1,unread:0}),
  };
  const {context,nodes,button} = setup(api);
  const oldRefresh = context.refreshNotifications();
  await context._deleteNotifications({});
  resolveOld({unread:1,items:[{id:1,event:'test',message:'Old notification',created_at:'now'}]});
  await oldRefresh;
  assert.match(nodes['notif-list'].innerHTML, /Nessuna notifica/);
  assert.equal(nodes['notif-badge'].style.display,'none');
  assert.equal(button.disabled,false);
});
test('delete failure restores controls and reports failure', async () => {
  const {context,button} = setup({get:async()=>({unread:0,items:[]}),post:async()=>{throw Error('failed');}});
  assert.equal(await context._deleteNotifications({}),null);
  assert.equal(button.disabled,false);
});
test('canceling clear-all does not delete notifications', async () => {
  let calls = 0;
  let state;
  state = setup({post:async()=>{calls++;}}, async()=>{
    assert.equal(state.nodes['notif-panel'].style.display,'none');
    return false;
  });
  await state.context.clearAllNotifications();
  assert.equal(calls,0);
  assert.equal(state.nodes['notif-panel'].style.display,'block');
});
test('single-delete and clear-all send the correct requests', async () => {
  const calls = [];
  const api = {get:async()=>({unread:0,items:[]}),post:async(path,body)=>{
    calls.push([path,JSON.parse(JSON.stringify(body))]);
    return {deleted:1,unread:0};
  }};
  const {context} = setup(api);
  await context.deleteNotification(7);
  await context.clearAllNotifications();
  assert.deepEqual(calls,[['/api/notifications/delete',{ids:[7]}],['/api/notifications/delete',{}]]);
});
