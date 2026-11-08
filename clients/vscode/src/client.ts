import { AgentEvent, coreOrigin, id, parsePreview, parseStatus, SSEDecoder } from './protocol';

async function boundedJson(response: Response): Promise<unknown> {
  if (!response.body) { throw new Error('Core response has no body.'); }
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) { break; }
      size += value.byteLength;
      if (size > 1000000) { throw new Error('Core response exceeds the size limit.'); }
      chunks.push(value);
    }
  } finally { await reader.cancel(); }
  const body = Buffer.concat(chunks);
  try { return JSON.parse(body.toString('utf8')) as unknown; }
  catch { throw new Error('Core returned invalid JSON.'); }
}

export class CoreClient {
  readonly origin: string;
  constructor(origin: string, private readonly token: string) {
    this.origin = coreOrigin(origin);
    if (token.length < 32 || /[\r\n]/.test(token)) { throw new Error('Configure a valid core API token.'); }
  }
  private async response(path: string, method: string, signal: AbortSignal, body?: unknown): Promise<Response> {
    if (!path.startsWith('/') || path.startsWith('//') || path.includes('..') || path.includes('\\')) {
      throw new Error('Invalid core API path.');
    }
    let response: Response;
    try {
      response = await fetch(this.origin + path, {
        method, signal, redirect: 'error',
        headers: { Authorization: `Bearer ${this.token}`, 'Content-Type': 'application/json' },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
    } catch {
      throw new Error(signal.aborted ? 'Core request cancelled or timed out.' :
        'Core connection failed. Check the configured address and server.');
    }
    if (!response.ok) {
      await response.body?.cancel();
      throw new Error(`Core HTTP ${response.status}. ${response.status === 401 ?
        'Configure the correct API token.' : response.status === 409 ?
        'State changed or policy blocked this action; refresh the session.' : 'Request rejected.'}`);
    }
    return response;
  }
  async request(path: string, method = 'GET', body?: unknown): Promise<unknown> {
    return boundedJson(await this.response(path, method, AbortSignal.timeout(15000), body));
  }
  async status(sessionId: string) { return parseStatus(await this.request(`/sessions/${id(sessionId)}/status`)); }
  async preview(sessionId: string, approvalId: string) {
    return parsePreview(await this.request(`/sessions/${id(sessionId)}/approvals/${id(approvalId)}/preview`));
  }
  async events(sessionId: string, after: number, signal: AbortSignal,
               accept: (event: AgentEvent) => Promise<void>): Promise<void> {
    if (!Number.isSafeInteger(after) || after < 0) { throw new Error('Invalid saved event cursor.'); }
    const idle = new AbortController();
    let timer = setTimeout(() => idle.abort(), 30000);
    const combined = AbortSignal.any([signal, idle.signal]);
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
    try {
      const response = await this.response(`/sessions/${id(sessionId)}/events?after=${after}`, 'GET', combined);
      if (!response.body) { throw new Error('Core event stream has no body.'); }
      reader = response.body.getReader();
      const decoder = new TextDecoder('utf-8', { fatal: true });
      const parser = new SSEDecoder();
      let cursor = after;
      while (true) {
        const { value, done } = await reader.read();
        if (!done) {
          clearTimeout(timer);
          timer = setTimeout(() => idle.abort(), 30000);
        }
        for (const event of parser.push(done ? decoder.decode() : decoder.decode(value, { stream: true }))) {
          if (event.session_id !== sessionId) { throw new Error('Core returned another session event.'); }
          if (event.id <= cursor) { continue; }
          await accept(event);
          cursor = event.id;
        }
        if (done) { parser.finish(); break; }
      }
    } finally { clearTimeout(timer); await reader?.cancel().catch(() => undefined); }
  }
}
