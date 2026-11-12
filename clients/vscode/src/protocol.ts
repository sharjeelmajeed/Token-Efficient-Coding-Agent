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
  /** Computed by the core on every request; older cores may omit both. */
  budget_limit_micro_usd?: number; budget_warning?: boolean;
  run:{ id: string; status: string; step: number; outcome: string | null } | null;
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
  if ((value.budget_limit_micro_usd !== undefined && !Number.isSafeInteger(value.budget_limit_micro_usd)) ||
      (value.budget_warning !== undefined && typeof value.budget_warning !== 'boolean')) {
    throw new Error('Invalid core budget status.');
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
export function usd(micro: unknown): string {
  return `$${(Number.isSafeInteger(micro) ? Number(micro) / 1e6 : 0).toFixed(4)}`;
}
/** Percent of the budget used, only when the core reports the warning; otherwise undefined. */
export function budgetWarningPercent(status: Status): number | undefined {
  const limit = status.budget_limit_micro_usd;
  if (status.budget_warning !== true || limit === undefined || limit <= 0) { return undefined; }
  return Math.floor((limit - status.budget_remaining_micro_usd) * 100 / limit);
}
export function statusLabel(status: Status): string {
  const model = status.usage.at(-1)?.model ?? 'not called';
  const privacy = status.confidential ? 'LOCAL ONLY' : 'standard';
  const used = budgetWarningPercent(status);
  const warning = used === undefined ? '' : ` · ⚠ ${used}% budget`;
  return `$(shield) ${safeText(model, 80)} · ${usd(status.budget_remaining_micro_usd)}${warning} · ${privacy}`;
}
/** Plain wording for the core's budget option codes (same as the CLI). Unknown codes are shown as sent. */
const BUDGET_OPTIONS: ReadonlyMap<string, string> = new Map([
  ['raise_budget_then_resubmit', 'raise your budget, then resend'],
  ['raise_gateway_budget_then_resubmit', 'raise the gateway budget, then resend'],
]);
/**
 * Readable text for a core event, built only from fields the core sent. Same wording as
 * the CLI. Missing or oddly typed fields never throw; an empty array means show nothing.
 */
export function noticeText(kind: string, payload: Record<string, unknown>): string[] {
  const field = (name: string, fallback = ''): string => {
    const value = payload[name];
    return value === undefined || value === null || value === '' ? fallback : safeText(value, 500);
  };
  const budget = (): string => {
    const options = Array.isArray(payload.options) && payload.options.length
      ? `; options: ${payload.options.map(o => BUDGET_OPTIONS.get(String(o)) ?? safeText(o, 60)).join(', ')}` : '';
    return `${usd(payload.remaining_micro_usd)} remaining of ${usd(payload.limit_micro_usd)}${options}`;
  };
  switch (kind) {
    case 'budget_warning': return [`Budget warning: ${budget()}`];
    case 'budget_hit': return [`Budget limit reached: ${field('reason', 'budget limit reached')}; ${budget()}`];
    case 'model_switched': {
      const line = `Model switched: ${field('from', 'unknown')} -> ${field('to', 'unknown')}`;
      return [field('reason') ? `${line} (${field('reason')})` : line];
    }
    case 'session_paused':
      return [`Paused: ${field('reason', 'no reason given')}`,
        ...(field('resume') ? [`Next step: ${field('resume')}`] : [])];
    case 'run_complete':
      return [`Run finished: ${field('outcome', 'unknown')}`, ...(field('note') ? [`Note: ${field('note')}`] : [])];
    case 'policy_block': return [`Blocked by policy: ${field('reason', 'no reason given')}`];
    case 'approval_expired': return [`Approval ${field('approval_id')} expired - the action was not run`];
    case 'research_used':
    {
      const query = field('query', 'unknown query');
      const cached = payload.cache_hit === true ? 'cached' : 'new';
      // The trigger says why research ran (e.g. unknown_dependency); shown as the core sent it.
      const trigger = field('trigger');
      return [trigger ? `Public research (${trigger}, ${cached}): ${query}` : `Public research: ${query} (${cached})`];
    }
    case 'tool_result': {
      const result = payload.result;
      if (record(result) && result.recoverable === true) {
        // Not "will retry": the model may choose another action, and three mistakes in a row pause the run.
        return [`Tool error (the agent can correct this): ${safeText(result.error || 'invalid tool input', 500)}`];
      }
      return [];
    }
    default: return [];
  }
}

/** Git tools the core gates behind an exact-action approval (GIT_TOOLS.md). Same set as the CLI. */
export const GIT_GATED_TOOLS: ReadonlySet<string> = new Set(['git_commit', 'git_checkout', 'git_push']);
const GIT_FIELDS = ['branch', 'create', 'remote', 'expected_head', 'message', 'paths'] as const;
/**
 * Read-only review text for a gated git action, built only from the core's stored action and
 * preview. Same order and wording as the CLI's git_summary(). The client never builds or edits
 * the command; it only approves the approval ID.
 */
export function gitApprovalText(action: Approval['action'], diff: string): string {
  const lines = [`Exact git action requires your approval: ${safeText(action.tool, 100)}`];
  for (const name of GIT_FIELDS) {
    if (!(name in action)) { continue; }
    const value = action[name];
    lines.push(`  ${name}: ${safeText(Array.isArray(value) ? value.map(String).join(', ') : value, 4000)}`);
  }
  const state = action.git;
  if (record(state) && Object.keys(state).length) {
    lines.push('  checked by core before running:');
    for (const [key, value] of Object.entries(state)) { lines.push(`    ${safeText(key, 100)}: ${safeText(value, 500)}`); }
  }
  // The core's preview: staged diff for a commit, files/branch for a checkout, commits and remote URL for a push.
  if (diff) { lines.push('', safeText(diff, 64000)); }
  return safeText(lines.join('\n'), 64000);
}

export interface TimelineEntry { kind: string; text: string }
/**
 * Notices the core sends after a reply's streamed pieces but before its final piece
 * (model_switched and budget_warning come right after the model call is settled).
 * They do not end the reply.
 */
export const MID_REPLY_EVENTS: ReadonlySet<string> = new Set(['model_switched', 'budget_warning']);
/**
 * Joins streamed message_delta pieces into one assistant entry. With streaming on, the core
 * sends partial: true pieces and then exactly one partial: false piece. Any other event before
 * that final piece, except the MID_REPLY_EVENTS notices, means the reply is incomplete. Without
 * a partial field, each delta is one whole message, as before.
 */
export class ReplyJoiner {
  private open: TimelineEntry | undefined;
  constructor(private readonly push: (entry: TimelineEntry) => void) {}
  delta(payload: Record<string, unknown>): void {
    const text = safeText(payload.text ?? '');
    if (this.open) {
      this.open.text = safeText(this.open.text + text);
      if (payload.partial !== true) { this.open = undefined; }
    } else if (payload.partial === true) {
      this.open = { kind: 'assistant', text };
      this.push(this.open);
    } else {
      this.push({ kind: 'assistant', text });
    }
  }
  /** Call for every non-delta event. Mid-reply notices keep the reply open; others interrupt it. */
  event(type: string): void {
    if (!MID_REPLY_EVENTS.has(type)) { this.interrupt(); }
  }
  /** Closes an open reply and marks it incomplete. */
  interrupt(): void {
    if (this.open) {
      this.open = undefined;
      this.push({ kind: 'warning', text: '(reply incomplete)' });
    }
  }
  /** Forget an open reply without marking it, e.g. when the timeline is cleared. */
  reset(): void { this.open = undefined; }
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
