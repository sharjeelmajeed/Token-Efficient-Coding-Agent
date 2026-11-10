import * as vscode from 'vscode';
import { randomBytes } from 'node:crypto';
import { CoreClient } from './client';
import { AgentEvent, ReplyJoiner, SessionState, Status, TimelineEntry, budgetWarningPercent, coreOrigin, id,
  noticeText, record, safeText, statusLabel, usd } from './protocol';
import { webviewHtml } from './webview';

class Documents implements vscode.TextDocumentContentProvider {
  private readonly values = new Map<string, string>();
  put(label: string, content: string): vscode.Uri {
    const uri = vscode.Uri.from({ scheme: 'token-agent', path: `/${randomBytes(12).toString('hex')}/${label}` });
    this.values.set(uri.toString(), content);
    if (this.values.size > 40) { const oldest = this.values.keys().next().value; if (oldest) { this.values.delete(oldest); } }
    return uri;
  }
  provideTextDocumentContent(uri: vscode.Uri): string {
    return this.values.get(uri.toString()) ?? 'This preview expired. Open the current approval again.';
  }
}

class AgentController implements vscode.Disposable {
  private panel: vscode.WebviewPanel | undefined;
  private stream: AbortController | undefined;
  private state: SessionState | undefined;
  private currentStatus: Status | undefined;
  private readonly timeline: TimelineEntry[] = [];
  private readonly replies = new ReplyJoiner(entry => this.push(entry));
  private readonly decisions = new Set<string>();
  private readonly documents = new Documents();
  private readonly bar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 20);
  private readonly registrations: vscode.Disposable[] = [];
  constructor(private readonly context: vscode.ExtensionContext) {
    this.bar.text = '$(shield) Token Agent';
    this.bar.command = 'tokenAgent.chat';
    this.bar.tooltip = 'Open the coding agent session';
    this.bar.show();
    this.registrations.push(vscode.workspace.registerTextDocumentContentProvider('token-agent', this.documents));
    const saved = context.workspaceState.get<SessionState>('session');
    if (saved && typeof saved.coreUrl === 'string' && typeof saved.repoPath === 'string' &&
        typeof saved.sessionId === 'string' && Number.isSafeInteger(saved.cursor) && saved.cursor >= 0) {
      try { id(saved.sessionId); coreOrigin(saved.coreUrl); this.state = saved; } catch { /* Invalid saved state is not reused. */ }
    }
    this.registrations.push(vscode.workspace.onDidChangeConfiguration(event => {
      if (event.affectsConfiguration('tokenAgent.coreUrl')) {
        this.stream?.abort(); this.state = undefined; this.currentStatus = undefined;
        void this.context.workspaceState.update('session', undefined);
        this.bar.text = '$(shield) Token Agent · reconnect';
        this.add('notice', 'Connection changed. Start a new session.');
      }
    }));
  }
  dispose(): void { this.stream?.abort(); this.panel?.dispose(); this.bar.dispose(); this.registrations.forEach(d => d.dispose()); }
  private trusted(): void {
    if (!vscode.workspace.isTrusted) { throw new Error('Trust this workspace before using Token Agent.'); }
  }
  private origin(): string {
    const setting = vscode.workspace.getConfiguration('tokenAgent').inspect<string>('coreUrl');
    return coreOrigin(setting?.globalValue ?? setting?.defaultValue ?? 'http://127.0.0.1:8765');
  }
  private async client(): Promise<CoreClient> {
    this.trusted();
    const origin = this.origin();
    const token = await this.context.secrets.get(`token-agent:${origin}`);
    if (!token) { throw new Error('Run Token Agent: Configure Connection first.'); }
    return new CoreClient(origin, token);
  }
  private active(): SessionState {
    this.trusted();
    if (!this.state || this.state.coreUrl !== this.origin() ||
        !vscode.workspace.workspaceFolders?.some(f => f.uri.scheme === 'file' && f.uri.fsPath === this.state?.repoPath)) {
      throw new Error('Start a session for the current trusted workspace.');
    }
    return this.state;
  }
  private add(kind: string, text: string): void { this.push({ kind, text: safeText(text) }); }
  private push(entry: TimelineEntry): void {
    this.timeline.push(entry);
    if (this.timeline.length > 200) { this.timeline.shift(); }
    this.render();
  }
  private render(): void {
    void this.panel?.webview.postMessage({ type: 'state', timeline: this.timeline,
      status: this.currentStatus, connected: Boolean(this.state) });
  }
  private async refresh(): Promise<Status> {
    const state = this.active();
    const status = await (await this.client()).status(state.sessionId);
    if (this.state !== state) { throw new Error('Session changed while refreshing.'); }
    this.currentStatus = status;
    this.bar.text = statusLabel(status);
    const used = budgetWarningPercent(status);
    this.bar.tooltip = `${safeText(status.mode)} · ${status.read_only ? 'read-only' : 'interactive'} · ${safeText(status.run?.status ?? 'idle')}` +
      (used === undefined ? '' : ` · Budget warning: ${used}% used (${usd(status.budget_remaining_micro_usd)} remaining of ${usd(status.budget_limit_micro_usd)})`);
    // Budget warning uses the error colour so it stays distinct from confidential mode's warning colour.
    this.bar.backgroundColor = used !== undefined ? new vscode.ThemeColor('statusBarItem.errorBackground')
      : status.confidential ? new vscode.ThemeColor('statusBarItem.warningBackground') : undefined;
    this.render();
    return status;
  }
  async configure(): Promise<void> {
    this.trusted();
    const entered = await vscode.window.showInputBox({ title: 'Trusted Token Agent core', value: this.origin(),
      prompt: 'HTTPS origin or loopback HTTP. This is a user setting, never a repository setting.',
      validateInput: value => { try { coreOrigin(value); return undefined; } catch (e) { return (e as Error).message; } } });
    if (entered === undefined) { return; }
    const origin = coreOrigin(entered);
    const token = await vscode.window.showInputBox({ title: 'Core API token', password: true, ignoreFocusOut: true,
      prompt: 'Stored in VS Code SecretStorage; never in workspace files.',
      validateInput: value => value.length >= 32 && !/[\r\n]/.test(value) ? undefined : 'Use a token of at least 32 characters.' });
    if (token === undefined) { return; }
    this.stream?.abort(); this.state = undefined; this.currentStatus = undefined;
    await this.context.workspaceState.update('session', undefined);
    await this.context.secrets.store(`token-agent:${origin}`, token);
    await vscode.workspace.getConfiguration('tokenAgent').update('coreUrl', origin, vscode.ConfigurationTarget.Global);
    this.add('notice', 'Connection saved. Start a session with the core running.');
  }
  async start(): Promise<void> {
    this.trusted();
    const folders = vscode.workspace.workspaceFolders?.filter(f => f.uri.scheme === 'file') ?? [];
    if (!folders.length) { throw new Error('Open a local folder, or a folder in a remote workspace host.'); }
    const folder = folders.length === 1 ? folders[0] : await vscode.window.showWorkspaceFolderPick();
    if (!folder) { return; }
    if (this.state) { await this.refresh(); }
    if (this.currentStatus?.run && ['running', 'waiting_approval'].includes(this.currentStatus.run.status)) {
      throw new Error('Cancel the active run before starting another session.');
    }
    const client = await this.client();
    const result = await client.request('/sessions', 'POST', { repo_path: folder.uri.fsPath });
    if (!record(result) || typeof result.session_id !== 'string') { throw new Error('Invalid session response.'); }
    this.stream?.abort();
    this.state = { sessionId: id(result.session_id), cursor: 0, coreUrl: client.origin, repoPath: folder.uri.fsPath };
    await this.context.workspaceState.update('session', this.state);
    this.timeline.length = 0; this.replies.reset();
    this.open();
    this.add('notice', `Session started for ${folder.name}.`);
    await this.refresh();
  }
  open(): void {
    this.trusted();
    if (this.panel) { this.panel.reveal(); return; }
    const media = vscode.Uri.joinPath(this.context.extensionUri, 'media');
    const panel = vscode.window.createWebviewPanel('tokenAgent.chat', 'Token Agent', vscode.ViewColumn.Beside,
      { enableScripts: true, localResourceRoots: [media], retainContextWhenHidden: true });
    this.panel = panel;
    panel.webview.html = webviewHtml(randomBytes(24).toString('base64'),
      panel.webview.asWebviewUri(vscode.Uri.joinPath(media, 'chat.js')).toString(),
      panel.webview.asWebviewUri(vscode.Uri.joinPath(media, 'chat.css')).toString(), panel.webview.cspSource);
    panel.onDidDispose(() => { if (this.panel === panel) { this.panel = undefined; } });
    panel.webview.onDidReceiveMessage((message: unknown) => {
      void this.handle(message).catch(error => this.reportError(error));
    });
  }
  private async handle(message: unknown): Promise<void> {
    if (!record(message) || typeof message.type !== 'string') { return; }
    if (message.type === 'ready') { this.render(); return; }
    this.trusted();
    if (message.type === 'start') { await this.start(); }
    else if (message.type === 'resume') { await this.resume(); }
    else if (message.type === 'cancel') { await this.cancel(); }
    else if (message.type === 'report') { await this.report(); }
    else if (message.type === 'task' && typeof message.text === 'string' && typeof message.research === 'boolean') {
      const text = message.text.trim();
      if (!text || text.length > 12000) { throw new Error('Enter a task of 1–12000 characters.'); }
      const state = this.active();
      await (await this.client()).request(`/sessions/${id(state.sessionId)}/messages`, 'POST',
        { text, allow_research: message.research });
      this.add('user', text); await this.resume();
    } else if (message.type === 'approval' && typeof message.id === 'string') {
      await this.review(message.id);
    }
  }
  private async receive(event: AgentEvent, state: SessionState): Promise<void> {
    if (this.state !== state) { return; }
    const p = event.payload;
    // Only the final streamed piece completes a reply. Other events mark it incomplete, except
    // model_switched and budget_warning, which the core sends before the final piece.
    if (event.type !== 'message_delta') { this.replies.event(event.type); }
    if (event.type === 'message_delta') { this.replies.delta(p); this.render(); }
    else if (event.type === 'tool_call' && record(p.action)) {
      this.add('tool', `Tool: ${safeText(p.action.tool, 100)}`);
    } else {
      const lines = noticeText(event.type, p);
      if (lines.length) {
        const text = lines.join('\n');
        const kind = event.type === 'run_complete' ? 'notice'
          : ['research_used', 'tool_result'].includes(event.type) ? 'tool' : 'warning';
        this.add(kind, text);
        if (['model_switched', 'budget_warning', 'budget_hit'].includes(event.type)) {
          void vscode.window.showWarningMessage(lines[0] ?? text);
        }
      }
    }
    state.cursor = event.id;
    await this.context.workspaceState.update('session', state);
    if (['approval_request', 'run_complete', 'session_paused', 'tool_result', 'model_switched',
      'budget_warning', 'budget_hit'].includes(event.type)) {
      await this.refresh();
    }
  }
  async resume(): Promise<void> {
    const state = this.active();
    this.open(); await this.refresh();
    this.stream?.abort();
    const controller = new AbortController(); this.stream = controller;
    const client = await this.client();
    void client.events(state.sessionId, state.cursor, controller.signal, event => this.receive(event, state))
      .then(async () => { if (!controller.signal.aborted && this.state === state) { await this.refresh(); } })
      .catch(error => { if (!controller.signal.aborted) { this.reportError(error); } });
  }
  private async review(approvalId: string): Promise<void> {
    id(approvalId);
    if (this.decisions.has(approvalId)) { return; }
    this.decisions.add(approvalId);
    try {
      const state = this.active(); const client = await this.client();
      const status = await this.refresh();
      const approval = status.pending_approvals.find(a => a.id === approvalId);
      if (!approval || status.read_only) { throw new Error('This approval is no longer available.'); }
      let detail: string;
      if (['apply_patch', 'write_file'].includes(approval.action.tool)) {
        const preview = await client.preview(state.sessionId, approvalId);
        if (preview.approval_id !== approvalId) { throw new Error('Approval preview identity mismatch.'); }
        const before = this.documents.put('before', preview.before);
        const after = this.documents.put('after', preview.after);
        await vscode.commands.executeCommand('vscode.diff', before, after, `Proposed change: ${safeText(preview.path, 200)}`);
        detail = `Review the native diff, then return to this decision. File: ${safeText(preview.path, 200)}`;
      } else { detail = safeText(JSON.stringify(approval.action, null, 2), 64000); }
      const choice = await vscode.window.showInformationMessage(
        approval.action.tool === 'run_shell' ? 'Review the exact command in chat before deciding.' : detail,
        'Accept once', 'Reject', 'Reject with feedback');
      if (!choice) { return; }
      let feedback = '';
      if (choice === 'Reject with feedback') {
        const entered = await vscode.window.showInputBox({ title: 'Feedback to the agent',
          validateInput: value => value.length <= 2000 ? undefined : 'Use at most 2000 characters.' });
        if (entered === undefined) { return; } feedback = entered;
      }
      if (choice === 'Accept once' && approval.action.tool === 'run_shell') {
        const confirmed = await vscode.window.showWarningMessage('Approve this exact isolated command?',
          { modal: true, detail }, 'Approve once');
        if (confirmed !== 'Approve once') { return; }
      }
      if (this.state !== state || this.origin() !== client.origin) { throw new Error('Connection changed; review again.'); }
      await client.request(`/sessions/${id(state.sessionId)}/approvals/${approvalId}`, 'POST', {
        decision: choice === 'Accept once' ? 'approve_once' : choice === 'Reject' ? 'deny' : 'deny_with_feedback', feedback,
      });
      await this.refresh();
    } finally { this.decisions.delete(approvalId); }
  }
  async cancel(): Promise<void> {
    const state = this.active();
    await (await this.client()).request(`/sessions/${id(state.sessionId)}/cancel`, 'POST');
    await this.refresh();
  }
  async report(): Promise<void> {
    const status = await this.refresh();
    if (!status.run) { throw new Error('No run report exists yet.'); }
    const report = await (await this.client()).request(`/metrics/runs/${id(status.run.id)}`);
    const uri = this.documents.put('run-report.json', JSON.stringify(report, null, 2));
    await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(uri), { preview: false });
  }
  reportError(error: unknown): void {
    const message = safeText(error instanceof Error ? error.message : 'Token Agent request failed.', 500);
    this.add('warning', message); void vscode.window.showErrorMessage(message);
  }
}

export function activate(context: vscode.ExtensionContext): void {
  const controller = new AgentController(context);
  context.subscriptions.push(controller);
  const commands: Record<string, () => void | Promise<void>> = {
    configure: () => controller.configure(), start: () => controller.start(), chat: () => controller.open(),
    resume: () => controller.resume(), cancel: () => controller.cancel(), report: () => controller.report(),
  };
  for (const [name, handler] of Object.entries(commands)) {
    context.subscriptions.push(vscode.commands.registerCommand(`tokenAgent.${name}`, () =>
      Promise.resolve().then(handler).catch(error => controller.reportError(error))));
  }
}
