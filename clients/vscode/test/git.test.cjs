const test = require('node:test');
const assert = require('node:assert/strict');
const { GIT_GATED_TOOLS, gitApprovalText } = require('../out/protocol');

// Same synthetic actions as the CLI's tests/test_cli.py, shaped like GIT_TOOLS.md.
const HEAD = 'a'.repeat(40);
const PUSH = { tool: 'git_push', remote: 'origin', branch: 'feature/x', expected_head: HEAD,
  git: { head: HEAD, remote_url: 'https://github.com/team/repo.git' } };
const COMMIT = { tool: 'git_commit', message: 'Fix calculator',
  paths: ['calculator.py', 'tests/test_calc.py'], expected_head: HEAD };

test('gated git tools match the core and the CLI', () => {
  assert.deepEqual([...GIT_GATED_TOOLS].sort(), ['git_checkout', 'git_commit', 'git_push']);
});

test('push text shows core fields in CLI order, the rechecked state and the preview', () => {
  const text = gitApprovalText(PUSH, 'abc123 Fix calculator\nto https://github.com/team/repo.git');
  assert.deepEqual(text.split('\n'), [
    'Exact git action requires your approval: git_push',
    '  branch: feature/x',
    '  remote: origin',
    `  expected_head: ${HEAD}`,
    '  checked by core before running:',
    `    head: ${HEAD}`,
    '    remote_url: https://github.com/team/repo.git',
    '',
    'abc123 Fix calculator',
    'to https://github.com/team/repo.git',
  ]);
});

test('commit text joins paths, skips missing fields and has no state section without state', () => {
  const diff = 'diff --git a/calculator.py b/calculator.py\n-    return a - b\n+    return a + b';
  const lines = gitApprovalText(COMMIT, diff).split('\n');
  assert.deepEqual(lines.slice(1, 4), [
    `  expected_head: ${HEAD}`, '  message: Fix calculator', '  paths: calculator.py, tests/test_calc.py']);
  assert.ok(!lines.includes('  checked by core before running:'));
  assert.ok(!lines.some(line => line.startsWith('  branch:') || line.startsWith('  remote:')));
  assert.deepEqual(lines.slice(4), ['', ...diff.split('\n')]);
});

test('control characters are removed and an empty preview adds nothing', () => {
  const checkout = { tool: 'git_checkout', branch: 'fix\x1b[31m', create: true, expected_head: HEAD,
    git: { 'br\x07anch': 'main\x1b' } };
  const lines = gitApprovalText(checkout, '').split('\n');
  assert.deepEqual(lines.slice(1, 3), ['  branch: fix[31m', '  create: true']);
  assert.deepEqual(lines.slice(-1), ['    branch: main']);
  assert.ok(!gitApprovalText(checkout, 'x\x1b[2Jy').includes('\x1b'));
});

test('the text is bounded and the action is not changed', () => {
  const action = { ...COMMIT, paths: [...COMMIT.paths] };
  const before = JSON.stringify(action);
  assert.ok(gitApprovalText(action, 'x'.repeat(200000)).length <= 64000);
  assert.equal(JSON.stringify(action), before);
});
