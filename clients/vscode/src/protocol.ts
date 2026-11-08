export interface AgentEvent {
  schema_version: 1; id: number; session_id: string; run_id: string | null;
  type: string; timestamp: string; payload: Record<string, unknown>;
}
export interface Approval {
  id: string; action: { tool: string; [key: string]: unknown }; diff: string; deadline: number;
}
export interface Status {
  session_id: string; mode: string; confidential: boolean; read_only: boolean;
  budget_remaining_micro_usd: number;
  run: { id: string; status: string; step: number; outcome: string | null } | null;
  pending_approvals: Approval[];
  usage: { model: string; input_tokens: number; output_tokens: number; amount: number }[];
}
export interface Preview {
  approval_id: string; path: string; before: string; after: string; expected_sha256: string;
}
export interface SessionState { sessionId: string; cursor: number; coreUrl: string; repoPath: string }

export function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
export function id(value: string): string {
  if (!/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value)) {
    throw new Error('Invalid core identifier. Start a new session.');
  }
  return value;
}
export function coreOrigin(value: string): string {
  let url: URL;
  try { url = new URL(value); } catch { throw new Error('Enter a valid core URL.'); }
  const loopback = ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname);
  if (url.username || url.password || url.search || url.hash || url.pathname !== '/' ||
      (url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback))) {
    throw new Error('Core must be an HTTPS origin or loopback HTTP, without credentials or a path.');
  }
  return url.origin;
}
export function safeText(value: unknown, limit = 20000): string {
  return String(value).replace(/[\x00-\x08\x0b-\x1f\x7f-\x9f]/g, '').slice(0, limit);
}
export function parseEvent(value: unknown): AgentEvent {
  if (!record(value) || value.schema_version !== 1 || !Number.isSafeInteger(value.id) ||
      Number(value.id) < 1 || typeof value.session_id !== 'string' ||
      !(value.run_id === null || typeof value.run_id === 'string') ||
      typeof value.type !== 'string' || typeof value.timestamp !== 'string' || !record(value.payload)) {
    throw new Error('Unsupported or invalid core event.');
  }
  id(value.session_id);
  if (value.run_id) { id(value.run_id); }
  return value as unknown as AgentEvent;
}
export function parseStatus(value: unknown): Status {
  if (!record(value) || typeof value.session_id !== 'string' || typeof value.mode !== 'string' ||
      typeof value.confidential !== 'boolean' || typeof value.read_only !== 'boolean' ||
      !Number.isSafeInteger(value.budget_remaining_micro_usd) ||
      !Array.isArray(value.pending_approvals) || !Array.isArray(value.usage) ||
      !(value.run === null || (record(value.run) && typeof value.run.id === 'string' &&
        typeof value.run.status === 'string' && Number.isSafeInteger(value.run.step)))) {
    throw new Error('Invalid core status response.');
  }
  id(value.session_id);
  if (record(value.run)) { id(String(value.run.id)); }
  for (const item of value.pending_approvals) {
    if (!record(item) || typeof item.id !== 'string' || !record(item.action) ||
        typeof item.action.tool !== 'string' || typeof item.diff !== 'string' ||
        typeof item.deadline !== 'number') { throw new Error('Invalid approval response.'); }
    id(item.id);
  }
  for (const item of value.usage) {
    if (!record(item) || typeof item.model !== 'string' ||
        ![item.input_tokens, item.output_tokens, item.amount].every(Number.isSafeInteger)) {
      throw new Error('Invalid usage response.');
    }
  }
  return value as unknown as Status;
}
export function parsePreview(value: unknown): Preview {
  if (!record(value) || !['approval_id', 'path', 'before', 'after', 'expected_sha256']
      .every(key => typeof value[key] === 'string')) { throw new Error('Invalid approval preview.'); }
  id(String(value.approval_id));
  return value as unknown as Preview;
}
export function statusLabel(status: Status): string {
  const model = status.usage.at(-1)?.model ?? 'not called';
  const privacy = status.confidential ? 'LOCAL ONLY' : 'standard';
  return `$(shield) ${safeText(model, 80)} · $${(status.budget_remaining_micro_usd / 1e6).toFixed(4)} · ${privacy}`;
}

export class SSEDecoder {
  private buffer = '';
  push(chunk: string): AgentEvent[] {
    this.buffer = (this.buffer + chunk).replace(/\r\n/g, '\n');
    const events: AgentEvent[] = [];
    let boundary: number;
    while ((boundary = this.buffer.indexOf('\n\n')) >= 0) {
      const frame = this.buffer.slice(0, boundary);
      this.buffer = this.buffer.slice(boundary + 2);
      if (frame.length > 512000) { throw new Error('Core event exceeds the size limit.'); }
      const lines = frame.split('\n');
      const body = lines.filter(line => line.startsWith('data:'))
        .map(line => line.slice(5).replace(/^ /, '')).join('\n');
      if (!body) { continue; }
      let value: unknown;
      try { value = JSON.parse(body); } catch { throw new Error('Invalid core event JSON.'); }
      const event = parseEvent(value);
      const cursor = lines.find(line => line.startsWith('id:'))?.slice(3).trim();
      if (cursor !== undefined && cursor !== String(event.id)) {
        throw new Error('Core event cursor mismatch.');
      }
      events.push(event);
    }
    if (this.buffer.length > 512000) { throw new Error('Core event exceeds the size limit.'); }
    return events;
  }
  finish(): void {
    if (this.buffer.trim()) { throw new Error('Stream ended in an incomplete event. Reconnect.'); }
  }
}
