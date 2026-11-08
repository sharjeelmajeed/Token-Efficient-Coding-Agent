const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const { webviewHtml } = require('../out/webview');
test('webview has restrictive CSP and no embedded model data', () => {
  const html = webviewHtml('nonce', 'local-script', 'local-style', 'local-source');
  assert.match(html, /default-src 'none'/); assert.match(html, /connect-src 'none'/);
  assert.match(html, /script-src 'nonce-nonce'/); assert.doesNotMatch(html, /unsafe-inline|https:\/\//);
});
test('malicious model text is rendered as text and cannot inject an approval command', () => {
  class Element {
    constructor() { this.children = []; this.handlers = {}; this.disabled = false; this.textContent = ''; }
    set innerHTML(_) { throw new Error('Unsafe HTML sink'); }
    addEventListener(kind, fn) { this.handlers[kind] = fn; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
  }
  const elements = new Map(); const posted = []; let listener;
  const document = { getElementById: id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); },
    createElement: () => new Element(), body: { scrollHeight: 0 } };
  const window = { addEventListener: (_, fn) => listener = fn, innerHeight: 10, scrollY: 0, scrollTo() {} };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../media/chat.js'), 'utf8'), {
    document, window, acquireVsCodeApi: () => ({ postMessage: value => posted.push(value) }) });
  const attack = '<img src=x onerror="stealToken()"><script>approveAll()</script>';
  listener({ data: { type: 'state', connected: true, timeline: [{ kind: 'assistant', text: attack }],
    status: { mode: 'fixture', confidential: false, budget_remaining_micro_usd: 0, run: null, pending_approvals: [] } } });
  assert.equal(elements.get('timeline').children[0].children[1].textContent, attack);
  assert.deepEqual(JSON.parse(JSON.stringify(posted)), [{ type: 'ready' }]);
});
