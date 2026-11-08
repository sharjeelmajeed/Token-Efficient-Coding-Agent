const assert = require('node:assert/strict');
const { CoreClient } = require('../out/client');
(async () => {
  const client = new CoreClient(process.env.TOKEN_AGENT_TEST_URL, process.env.TOKEN_AGENT_TEST_TOKEN);
  const created = await client.request('/sessions', 'POST', { repo_path: process.env.TOKEN_AGENT_TEST_REPO });
  const sid = created.session_id;
  await client.request(`/sessions/${sid}/messages`, 'POST', { text: 'demo: fix calculator addition' });
  let status;
  for (let i = 0; i < 200; i++) {
    status = await client.status(sid);
    if (status.pending_approvals.length) break;
    await new Promise(r => setTimeout(r, 10));
  }
  const approval = status.pending_approvals[0];
  assert.ok(approval);
  const preview = await client.preview(sid, approval.id);
  assert.match(preview.before, /return a - b/);
  assert.match(preview.after, /return a \+ b/);
  const events = [];
  const stream = client.events(sid, 0, AbortSignal.timeout(10000), async event => events.push(event));
  await client.request(`/sessions/${sid}/approvals/${approval.id}`, 'POST', { decision: 'approve_once' });
  await stream;
  status = await client.status(sid);
  assert.equal(status.run.status, 'completed');
  assert.ok(events.some(event => event.type === 'approval_consumed'));
  assert.ok(events.some(event => event.type === 'run_complete'));
  const resumed = [];
  await client.events(sid, events.at(-1).id, AbortSignal.timeout(10000), async event => resumed.push(event));
  assert.equal(resumed.length, 0);
  const report = await client.request(`/metrics/runs/${status.run.id}`);
  assert.equal(report.benchmark_evidence, false);
  process.stdout.write(JSON.stringify({ outcome: status.run.outcome, events: events.length }));
})().catch(() => { process.stderr.write('TypeScript/core integration failed.'); process.exitCode = 1; });
