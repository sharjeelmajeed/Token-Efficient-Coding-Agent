const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const { parseStatus, statusLabel, budgetWarningPercent, noticeText } = require('../out/protocol');

const SID = '12345678-1234-1234-1234-123456789abc';
const base = { session_id: SID, mode: 'bifrost', confidential: false, read_only: false,
  budget_remaining_micro_usd: 1000, run: null, pending_approvals: [],
  usage: [{ model: 'cloud/primary', input_tokens: 5000, output_tokens: 0, amount: 5000 }] };

test('status accepts cores with and without budget fields and rejects wrong types', () => {
  assert.equal(parseStatus(base).budget_warning, undefined);
  const full = parseStatus({ ...base, budget_limit_micro_usd: 6000, budget_warning: true });
  assert.equal(full.budget_warning, true);
  assert.throws(() => parseStatus({ ...base, budget_warning: 'true' }), /budget/);
  assert.throws(() => parseStatus({ ...base, budget_limit_micro_usd: 1.5 }), /budget/);
});

test('status bar shows the budget warning only when the core reports it', () => {
  const warned = parseStatus({ ...base, budget_limit_micro_usd: 6000, budget_warning: true });
  assert.equal(budgetWarningPercent(warned), 83);
  assert.match(statusLabel(warned), /cloud\/primary · \$0\.0010 · ⚠ 83% budget · standard/);
  for (const status of [base, { ...base, budget_limit_micro_usd: 6000, budget_warning: false },
    { ...base, budget_warning: true }, { ...base, budget_limit_micro_usd: 0, budget_warning: true }]) {
    assert.equal(budgetWarningPercent(parseStatus(status)), undefined);
    assert.doesNotMatch(statusLabel(parseStatus(status)), /budget/);
  }
});

test('notices use the same readable wording as the CLI', () => {
  const cases = [
    ['budget_warning', { remaining_micro_usd: 1000, limit_micro_usd: 6000, warning_percent: 80, options: ['continue', 'cancel'] },
      ['Budget warning: $0.0010 remaining of $0.0060; options: continue, cancel']],
    ['budget_hit', { reason: 'Budget exhausted', remaining_micro_usd: 0, limit_micro_usd: 6000, options: ['raise_budget_then_resubmit'] },
      ['Budget limit reached: Budget exhausted; $0.0000 remaining of $0.0060; options: raise your budget, then resend']],
    ['budget_hit', { reason: 'gateway_budget_hit', remaining_micro_usd: 0, limit_micro_usd: 6000,
      options: ['raise_gateway_budget_then_resubmit', 'toString', 'new_option'] },
      ['Budget limit reached: gateway_budget_hit; $0.0000 remaining of $0.0060; options: raise the gateway budget, then resend, toString, new_option']],
    ['model_switched', { from: 'cloud/a', to: 'cloud/b', reason: 'fallback' }, ['Model switched: cloud/a -> cloud/b (fallback)']],
    ['model_switched', { from: 'cloud/a', to: 'cloud/b' }, ['Model switched: cloud/a -> cloud/b']],
    ['session_paused', { reason: 'All providers failed', resume: 'Submit a new instruction' },
      ['Paused: All providers failed', 'Next step: Submit a new instruction']],
    ['session_paused', { reason: 'Budget' }, ['Paused: Budget']],
    ['run_complete', { outcome: 'completed_unverified', note: 'No checks configured' },
      ['Run finished: completed_unverified', 'Note: No checks configured']],
    ['policy_block', { reason: 'Path outside repository' }, ['Blocked by policy: Path outside repository']],
    ['approval_expired', { approval_id: 'abc' }, ['Approval abc expired - the action was not run']],
    ['research_used', { query: 'httpx timeout', cache_hit: true }, ['Public research: httpx timeout (cached)']],
    ['research_used', { query: 'httpx timeout', cache_hit: false }, ['Public research: httpx timeout (new)']],
    ['tool_result', { tool: 'read_file', result: { error: 'No such file', recoverable: true, changed: false } },
      ['Tool error (agent will retry): No such file']],
    ['tool_result', { tool: 'read_file', result: { text: 'contents' } }, []],
    ['unknown_future_event', { anything: 1 }, []],
  ];
  for (const [kind, payload, expected] of cases) { assert.deepEqual(noticeText(kind, payload), expected, kind); }
});

test('notices never throw on missing or odd fields and strip control characters', () => {
  const odd = { from: null, to: 5, reason: ['x'], resume: null, outcome: {}, result: null, options: 'continue',
    remaining_micro_usd: 'lots', query: '\x1b[31mred\x1b[0m' };
  for (const kind of ['budget_warning', 'budget_hit', 'model_switched', 'session_paused', 'run_complete',
    'policy_block', 'approval_expired', 'research_used', 'tool_result']) {
    assert.ok(Array.isArray(noticeText(kind, {})));
    for (const line of noticeText(kind, odd)) { assert.doesNotMatch(line, /\x1b/); }
  }
});

function renderWebview(status) {
  class Element {
    constructor() { this.children = []; this.handlers = {}; this.disabled = false; this.hidden = false; this.textContent = ''; }
    set innerHTML(_) { throw new Error('Unsafe HTML sink'); }
    addEventListener(kind, fn) { this.handlers[kind] = fn; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
  }
  const elements = new Map(); let listener;
  const document = { getElementById: id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); },
    createElement: () => new Element(), body: { scrollHeight: 0 } };
  const window = { addEventListener: (_, fn) => listener = fn, innerHeight: 10, scrollY: 0, scrollTo() {} };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../media/chat.js'), 'utf8'), {
    document, window, acquireVsCodeApi: () => ({ postMessage() {} }) });
  listener({ data: { type: 'state', connected: true, timeline: [], status } });
  return elements.get('budget');
}

test('chat header shows the budget warning only when the core reports it', () => {
  const shown = renderWebview({ ...base, budget_limit_micro_usd: 6000, budget_warning: true });
  assert.equal(shown.hidden, false);
  assert.equal(shown.textContent, '⚠ Budget warning: 83% used ($0.0010 remaining of $0.0060)');
  for (const status of [base, { ...base, budget_limit_micro_usd: 6000, budget_warning: false }, undefined]) {
    const hidden = renderWebview(status);
    assert.equal(hidden.hidden, true); assert.equal(hidden.textContent, '');
  }
});
