const test = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');

const SID = '11111111-1111-4111-8111-111111111111';
const TOKEN = 'test-core-token-' + 'x'.repeat(40);
const ORIGIN = 'http://127.0.0.1:8765';
const disposable = () => ({ dispose() {} });

function harness({ trusted = true, token = TOKEN, window = {}, workspace = {}, commands = {} } = {}) {
  const callbacks = new Map();
  const messages = [];
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
      ...workspace,
    },
    window: {
      createStatusBarItem: () => ({ show() {}, dispose() {} }),
      showErrorMessage: message => errors.push(message),
      createWebviewPanel: () => ({ reveal() {}, dispose() {}, onDidDispose() {},
        webview: { asWebviewUri: value => value, cspSource: 'vscode-webview:', postMessage() {},
          onDidReceiveMessage: callback => { messages.push(callback); } } }),
      ...window,
    },
    commands: { registerCommand: (name, callback) => { callbacks.set(name, callback); return disposable(); }, ...commands },
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
  // Sends a message as the chat webview would; the controller handles it without awaiting.
  const post = message => messages.at(-1)(message);
  return { callbacks, errors, storage, post, change: () => change({ affectsConfiguration: () => true }),
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

const AID = '22222222-2222-4222-8222-222222222222';
const HEAD = 'a'.repeat(40);
const settle = async () => { for (let i = 0; i < 50; i++) { await new Promise(resolve => setImmediate(resolve)); } };

for (const [tool, action, diff, file, choice, decision] of [
  ['git_commit', { tool: 'git_commit', message: 'Fix calculator', paths: ['calculator.py'], expected_head: HEAD },
    'diff --git a/calculator.py b/calculator.py\n+    return a + b', 'git-commit.diff', 'Accept once', 'approve_once'],
  ['git_push', { tool: 'git_push', remote: 'origin', branch: 'feature/x', expected_head: HEAD,
    git: { head: HEAD, remote_url: 'https://github.com/team/repo.git' } },
    'abc123 Fix calculator', 'git-push.txt', 'Reject', 'deny'],
]) {
  test(`${tool} approval opens the core's preview read-only and posts only the decision`, async () => {
    const opened = [];
    const shown = [];
    const executed = [];
    let content;
    const h = harness({
      window: {
        showTextDocument: async (doc, options) => { shown.push(options); },
        showInformationMessage: async (message, ...buttons) => {
          assert.equal(message, 'Review the git action in the opened document, then decide.');
          assert.deepEqual(buttons, ['Accept once', 'Reject', 'Reject with feedback']);
          return choice;
        },
      },
      workspace: {
        registerTextDocumentContentProvider: (scheme, provider) => { content = uri => provider.provideTextDocumentContent(uri); return disposable(); },
        openTextDocument: async uri => { opened.push(uri); return { uri }; },
      },
      commands: { executeCommand: async (...args) => { executed.push(args); } },
    });
    const original = global.fetch;
    const calls = [];
    global.fetch = async (url, options = {}) => {
      calls.push({ url, method: options.method ?? 'GET', body: options.body });
      if (url.endsWith('/status')) {
        return Response.json({ session_id: SID, mode: 'fixture', confidential: false, read_only: false,
          budget_remaining_micro_usd: 1000000, run: null, usage: [],
          pending_approvals: [{ id: AID, action, diff, deadline: 9e12 }] });
      }
      return Response.json(url.endsWith('/sessions') ? { session_id: SID } : { ok: true });
    };
    try {
      await h.callbacks.get('tokenAgent.start')();
      h.post({ type: 'approval', id: AID });
      await settle();
      assert.deepEqual(h.errors, []);
      assert.equal(opened.length, 1);
      assert.ok(JSON.parse(opened[0].toString()).path.endsWith(`/${file}`));
      assert.deepEqual(shown, [{ preview: false }]);
      const text = content(opened[0]);
      assert.match(text, new RegExp(`^Exact git action requires your approval: ${tool}\n`));
      assert.ok(text.endsWith(`\n\n${diff}`));
      assert.deepEqual(executed, [], 'no native diff and no editor command for git');
      assert.ok(!calls.some(call => call.url.includes('/preview')), 'git uses the approval diff, not /preview');
      const posts = calls.filter(call => call.method === 'POST' && call.url.includes('/approvals/'));
      assert.equal(posts.length, 1);
      assert.equal(posts[0].url, `${ORIGIN}/sessions/${SID}/approvals/${AID}`);
      assert.deepEqual(JSON.parse(posts[0].body), { decision, feedback: '' });
    } finally { global.fetch = original; h.dispose(); }
  });
}
