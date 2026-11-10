const test = require('node:test');
const assert = require('node:assert/strict');
const { ReplyJoiner } = require('../out/protocol');

function joiner() {
  const timeline = [];
  const replies = new ReplyJoiner(entry => timeline.push(entry));
  // Snapshot the plain values the webview would receive.
  return { replies, view: () => JSON.parse(JSON.stringify(timeline)) };
}

test('streamed pieces join into one assistant entry and the final piece closes it', () => {
  const { replies, view } = joiner();
  replies.delta({ text: 'Fixed ', partial: true });
  replies.delta({ text: 'the calc', partial: true });
  assert.deepEqual(view(), [{ kind: 'assistant', text: 'Fixed the calc' }]);
  replies.delta({ text: 'ulator.', partial: false });
  replies.interrupt(); // a later event after a complete reply adds nothing
  assert.deepEqual(view(), [{ kind: 'assistant', text: 'Fixed the calculator.' }]);
});

test('an empty final piece still closes the reply', () => {
  const { replies, view } = joiner();
  replies.delta({ text: 'Done', partial: true });
  replies.delta({ text: '', partial: false });
  replies.delta({ text: 'Next reply', partial: true });
  assert.deepEqual(view(), [{ kind: 'assistant', text: 'Done' }, { kind: 'assistant', text: 'Next reply' }]);
});

test('an interrupting event marks the reply incomplete exactly once', () => {
  const { replies, view } = joiner();
  replies.delta({ text: 'Half a rep', partial: true });
  replies.interrupt(); // e.g. policy_block
  replies.interrupt(); // e.g. session_paused
  assert.deepEqual(view(), [{ kind: 'assistant', text: 'Half a rep' }, { kind: 'warning', text: '(reply incomplete)' }]);
});

test('model_switched and budget_warning between the pieces keep the reply open', () => {
  // Core order (engine.execute): partial pieces, settle, model_switched, budget_warning, final piece.
  const { replies, view } = joiner();
  replies.delta({ text: 'Fixed ', partial: true });
  replies.event('model_switched');
  replies.event('budget_warning');
  replies.delta({ text: 'it.', partial: false });
  assert.deepEqual(view(), [{ kind: 'assistant', text: 'Fixed it.' }]);
});

test('other events through event() still mark the reply incomplete', () => {
  const { replies, view } = joiner();
  replies.delta({ text: 'Half', partial: true });
  replies.event('budget_warning');
  replies.event('policy_block');
  replies.event('session_paused');
  assert.deepEqual(view(), [{ kind: 'assistant', text: 'Half' }, { kind: 'warning', text: '(reply incomplete)' }]);
});

test('streaming off: each delta is one whole message, as before', () => {
  const { replies, view } = joiner();
  replies.delta({ text: 'First' });
  replies.interrupt();
  replies.delta({ text: 'Second' });
  assert.deepEqual(view(), [{ kind: 'assistant', text: 'First' }, { kind: 'assistant', text: 'Second' }]);
});

test('two streamed replies in a row become two entries', () => {
  const { replies, view } = joiner();
  for (const reply of ['One', 'Two']) {
    replies.delta({ text: reply, partial: true });
    replies.delta({ text: '!', partial: false });
  }
  assert.deepEqual(view().map(e => e.text), ['One!', 'Two!']);
});

test('reset forgets an open reply without marking it', () => {
  const { replies, view } = joiner();
  replies.delta({ text: 'Old session', partial: true });
  replies.reset();
  replies.interrupt();
  replies.delta({ text: 'New', partial: true });
  assert.deepEqual(view().map(e => e.text), ['Old session', 'New']);
});

test('joined text keeps the length limit and stays plain, control-free text', () => {
  const { replies, view } = joiner();
  for (let i = 0; i < 30; i++) { replies.delta({ text: 'x'.repeat(1000), partial: true }); }
  replies.delta({ text: '<script>approveAll()</script>\x1b[31m', partial: false });
  const [entry] = view();
  assert.equal(entry.text.length, 20000);
  const { replies: other, view: otherView } = joiner();
  other.delta({ text: '<img onerror=x>\x1b[31m', partial: true });
  other.delta({ text: null, partial: false });
  assert.deepEqual(otherView(), [{ kind: 'assistant', text: '<img onerror=x>[31m' }]);
});
