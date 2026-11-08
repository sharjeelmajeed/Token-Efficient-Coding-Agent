const test = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');

const SID = '11111111-1111-4111-8111-111111111111';
const TOKEN = 'test-core-token-' + 'x'.repeat(40);
const ORIGIN = 'http://127.0.0.1:8765';
const disposable = () => ({ dispose() {} });

function harness({ trusted = true, token = TOKEN } = {}) {
  const callbacks = new Map();
  const errors = [];
  const storage = new Map();
  let change;
  const fake = {
    StatusBarAlignment: { Left: 1 }, ViewColumn: { Beside: 2 },
    Uri: { joinPath: (...args) => args.join('/'), from: value => ({ toString: () => JSON.stringify(value) }) },
    workspace: {
      isTrusted: trusted, workspaceFolders: [{ name: 'repo', uri: { scheme: 'file', fsPath: '/repo' } }],
      registerTextDocumentContentProvider: disposable,
      getConfiguration: () => ({ inspect: () => ({ defaultValue: ORIGIN, workspaceValue: 'https://evil.invalid' }) }),
      onDidChangeConfiguration: callback => { change = callback; return disposable(); },
    },
    window: {
      createStatusBarItem: () => ({ show() {}, dispose() {} }),
      showErrorMessage: message => errors.push(message),
      createWebviewPanel: () => ({ reveal() {}, dispose() {}, onDidDispose() {},
        webview: { asWebviewUri: value => value, cspSource: 'vscode-webview:', postMessage() {}, onDidReceiveMessage() {} } }),
    },
    commands: { registerCommand: (name, callback) => { callbacks.set(name, callback); return disposable(); } },
  };
  const context = { subscriptions: [], extensionUri: '/extension',
    workspaceState: { get: key => storage.get(key), update: async (key, value) => storage.set(key, value) },
    secrets: { get: async key => { assert.equal(key, `token-agent:${ORIGIN}`); return token; } },
  };
  const original = Module._load;
  Module._load = function (name, ...args) { return name === 'vscode' ? fake : original.call(this, name, ...args); };
  try {
    delete require.cache[require.resolve('../out/extension')];
    require('../out/extension').activate(context);
  } finally { Module._load = original; }
  return { callbacks, errors, storage, change: () => change({ affectsConfiguration: () => true }),
    dispose: () => context.subscriptions.forEach(item => item.dispose()) };
}

test('untrusted workspace cannot start or configure a core connection', async () => {
  const h = harness({ trusted: false });
  try {
    await h.callbacks.get('tokenAgent.start')();
    await h.callbacks.get('tokenAgent.configure')();
    assert.equal(h.errors.length, 2);
    assert.ok(h.errors.every(message => message.includes('Trust this workspace')));
    assert.equal(h.storage.size, 0);
  } finally { h.dispose(); }
});

test('missing credentials prevent network access', async () => {
  const h = harness({ token: null });
  try {
    await h.callbacks.get('tokenAgent.start')();
    assert.match(h.errors[0], /Configure Connection first/);
    assert.equal(h.storage.size, 0);
  } finally { h.dispose(); }
});

test('repository settings cannot steal the token and connection changes invalidate a session', async () => {
  const h = harness();
  const original = global.fetch;
  const calls = [];
  global.fetch = async (url, options) => {
    calls.push(url);
    assert.ok(url.startsWith(ORIGIN + '/'));
    assert.equal(options.headers.Authorization, `Bearer ${TOKEN}`);
    return Response.json(url.endsWith('/status') ? {
      session_id: SID, mode: 'fixture', confidential: false, read_only: false,
      budget_remaining_micro_usd: 1000000, run: null, pending_approvals: [], usage: [],
    } : { session_id: SID });
  };
  try {
    await h.callbacks.get('tokenAgent.start')();
    assert.equal(h.errors.length, 0);
    assert.equal(h.storage.get('session').sessionId, SID);
    assert.equal(calls.length, 2);
    h.change();
    await h.callbacks.get('tokenAgent.cancel')();
    assert.equal(h.storage.get('session'), undefined);
    assert.equal(calls.length, 2);
    assert.match(h.errors.at(-1), /Start a session/);
  } finally { global.fetch = original; h.dispose(); }
});
