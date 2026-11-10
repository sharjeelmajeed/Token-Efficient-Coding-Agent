(() => {
  'use strict';
  const vscode = acquireVsCodeApi();
  const byId = id => document.getElementById(id);
  const post = type => vscode.postMessage({ type });
  ['start', 'resume', 'cancel', 'report'].forEach(id => byId(id).addEventListener('click', () => post(id)));
  byId('task-form').addEventListener('submit', event => {
    event.preventDefault();
    const text = byId('task').value.trim();
    if (text) { vscode.postMessage({ type: 'task', text, research: byId('research').checked }); }
  });
  window.addEventListener('message', event => {
    const data = event.data;
    if (!data || data.type !== 'state') { return; }
    const status = data.status;
    byId('status').textContent = status ? `${status.mode} · ${status.confidential ? 'LOCAL ONLY' : 'standard'} · ${status.run?.status ?? 'idle'} · $${(status.budget_remaining_micro_usd / 1000000).toFixed(4)} remaining` : 'Start a session to connect.';
    // Shown only when the core reports the warning; the webview never works it out itself.
    const limit = status?.budget_limit_micro_usd;
    const warn = status?.budget_warning === true && Number.isSafeInteger(limit) && limit > 0;
    const budget = byId('budget');
    budget.hidden = !warn;
    budget.textContent = warn
      ? `⚠ Budget warning: ${Math.floor((limit - status.budget_remaining_micro_usd) * 100 / limit)}% used ` +
        `($${(status.budget_remaining_micro_usd / 1000000).toFixed(4)} remaining of $${(limit / 1000000).toFixed(4)})`
      : '';
    const busy = ['running', 'waiting_approval'].includes(status?.run?.status);
    byId('send').disabled = !data.connected || busy;
    byId('cancel').disabled = !busy;
    byId('report').disabled = !status?.run;
    const timeline = byId('timeline');
    const nearBottom = window.innerHeight + window.scrollY >= document.body.scrollHeight - 160;
    const nodes = (Array.isArray(data.timeline) ? data.timeline : []).map(entry => {
      const article = document.createElement('article');
      article.className = ['user', 'assistant', 'tool', 'warning', 'notice'].includes(entry.kind) ? entry.kind : 'notice';
      const label = document.createElement('strong'); label.textContent = entry.kind;
      const content = document.createElement('pre'); content.textContent = entry.text;
      article.append(label, content); return article;
    });
    timeline.replaceChildren(...nodes);
    const approvals = (status?.pending_approvals ?? []).map(approval => {
      const card = document.createElement('div'); card.className = 'approval';
      const title = document.createElement('strong'); title.textContent = `Approval required: ${approval.action.tool}`;
      const details = document.createElement('pre');
      details.textContent = approval.diff || JSON.stringify(approval.action, null, 2);
      const button = document.createElement('button'); button.textContent = 'Review and decide';
      button.addEventListener('click', () => vscode.postMessage({ type: 'approval', id: approval.id }));
      card.append(title, details, button); return card;
    });
    byId('approvals').replaceChildren(...approvals);
    if (nearBottom) { window.scrollTo(0, document.body.scrollHeight); }
  });
  post('ready');
})();
