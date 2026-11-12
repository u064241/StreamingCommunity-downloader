const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');
const source = fs.readFileSync('app/static/panel.js', 'utf8');
// The real translations, in Italian, the way the other frontend tests load them.
function italian() {
  const i18n = vm.createContext({
    window: {}, document: {addEventListener() {}},
    localStorage: {getItem: () => 'it'}, navigator: {language: 'it'},
  });
  vm.runInContext(fs.readFileSync('app/static/i18n.js', 'utf8'), i18n);
  return i18n.window.t;
}
function setup(api, confirm = async () => true) {
  const nodes = Object.fromEntries(['notif-badge','notif-list','notif-actions','notif-panel'].map(id => [id,{style:{display:'block'},innerHTML:'',textContent:''}]));
  const button = {disabled:false};
  // Minimal browser globals panel.js touches at load time: the language-change
  // listener on window and the update-badge poll. The poll is recorded, never run.
  const intervals = [];
  const context = vm.createContext({api, scConfirm:confirm,
    window:{addEventListener:()=>{}, t:italian()},
    setInterval:(fn, ms)=>{intervals.push({fn, ms}); return intervals.length;},
    clearInterval:()=>{},
    document:{getElementById:id=>nodes[id], querySelectorAll:()=>[button], querySelector:()=>null,
      addEventListener:()=>{}, visibilityState:'visible'},
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
