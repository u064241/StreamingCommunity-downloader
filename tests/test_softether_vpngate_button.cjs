// The "Mostra server VPN Gate" button is off until SoftEther is installed, and
// the list does not load (nor its IP check) while the button is off.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const html = fs.readFileSync('app/templates/pages/_settings.html', 'utf8').replace(/\r\n/g, '\n');
const js = fs.readFileSync('app/static/page-settings.js', 'utf8').replace(/\r\n/g, '\n');

test('the VPN Gate button starts disabled', () => {
  const tag = html.match(/<button[^>]*id="softether-vpngate-show"[^>]*>/);
  assert.ok(tag, 'button with id softether-vpngate-show is missing');
  assert.match(tag[0], /\sdisabled[\s>]/);
});

test('the status sets the button from installation', () => {
  assert.match(js, /vpnGateShow\.disabled = !available;/);
  assert.match(js, /vpnGateShow\.title = available \? '' : t\('softether\.install'\)/);
});

test('the list does not load while the button is off', () => {
  const start = js.indexOf('async function loadVpnGateServers');
  assert.ok(start >= 0);
  const body = js.slice(start, start + 800);
  assert.match(body, /if \(showButton && showButton\.disabled\) return;/);
  assert.ok(body.indexOf('showButton.disabled') < body.indexOf('softetherApi.vpngate()'));
});
