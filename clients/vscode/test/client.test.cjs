const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const { once } = require('node:events');
const { CoreClient } = require('../out/client');
const { coreOrigin, SSEDecoder, statusLabel, parseStatus } = require('../out/protocol');
const SID = '12345678-1234-1234-1234-123456789abc';
const TOKEN = 'a'.repeat(40);
const event = n => ({ schema_version: 1, id: n, session_id: SID, run_id: null,
  type: 'message_delta', timestamp: '2026-11-03T00:00:00Z', payload: { text: 'hello 🙂' } });
async function serve(handler, action) {
  const server = http.createServer(handler); server.listen(0, '127.0.0.1'); await once(server, 'listening');
  try { await action(`http://127.0.0.1:${server.address().port}`); }
  finally { server.closeAllConnections(); await new Promise(r => server.close(r)); }
}
test('unsafe origins cannot receive credentials', () => {
  for (const url of ['http://external.test', 'https://user:pass@host.test', 'https://host.test/?x=1',
                     'file:///tmp', 'https://host.test/api', 'http://127.0.0.1.evil.test']) {
    assert.throws(() => coreOrigin(url));
  }
  assert.equal(coreOrigin('http://127.0.0.1:8765/'), 'http://127.0.0.1:8765');
});
test('SSE handles fragmented CRLF, multiline data and heartbeat', () => {
  const parser = new SSEDecoder();
  const wire = ': heartbeat\r\n\r\nid: 1\r\ndata: ' + JSON.stringify(event(1)) + '\r\n\r\n';
  const events = [];
  for (const char of wire) events.push(...parser.push(char));
  parser.finish(); assert.deepEqual(events, [event(1)]);
});
test('invalid, oversize, mismatched and incomplete SSE frames fail safely', () => {
  assert.throws(() => new SSEDecoder().push('x'.repeat(512001)));
  assert.throws(() => new SSEDecoder().push('data: invalid\n\n'));
  assert.throws(() => new SSEDecoder().push(`id: 2\ndata: ${JSON.stringify(event(1))}\n\n`));
  const parser = new SSEDecoder(); parser.push('data: {'); assert.throws(() => parser.finish());
  assert.throws(() => new SSEDecoder().push(`data: ${JSON.stringify({ ...event(1), schema_version: 2 })}\n\n`));
});
test('redirects and provider error bodies never leak the token', async () => {
  let calls = 0;
  await serve((req, res) => { calls++; assert.equal(req.headers.authorization, `Bearer ${TOKEN}`);
    res.writeHead(302, { Location: '/leak' }); res.end(TOKEN); }, async origin => {
    await assert.rejects(new CoreClient(origin, TOKEN).request('/health'), error => !error.message.includes(TOKEN));
  });
  assert.equal(calls, 1);
});
test('JSON response is bounded', async () => {
  await serve((req, res) => { res.end('x'.repeat(1000001)); }, async origin => {
    await assert.rejects(new CoreClient(origin, TOKEN).request('/health'), /size limit/);
  });
});
test('SSE cursor, duplicate suppression and UTF8 work over real HTTP', async () => {
  await serve((req, res) => {
    assert.match(req.url, /after=1/);
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    const wire = Buffer.from([event(1), event(2), event(3)].map(e => `id: ${e.id}\ndata: ${JSON.stringify(e)}\n\n`).join(''));
    for (const byte of wire) res.write(Buffer.from([byte])); res.end();
  }, async origin => {
    const received = [];
    await new CoreClient(origin, TOKEN).events(SID, 1, AbortSignal.timeout(3000), async e => received.push(e));
    assert.deepEqual(received.map(e => e.id), [2, 3]); assert.equal(received[0].payload.text, 'hello 🙂');
  });
});
test('status schema and budget/privacy label', () => {
  assert.throws(() => parseStatus({}));
  const status = parseStatus({ session_id: SID, mode: 'fixture', confidential: true, read_only: false,
    budget_remaining_micro_usd: 120000, run: null, pending_approvals: [],
    usage: [{ model: 'local/code', input_tokens: 3, output_tokens: 4, amount: 0 }] });
  assert.match(statusLabel(status), /local\/code.*\$0.1200.*LOCAL ONLY/);
});
