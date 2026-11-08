export function webviewHtml(nonce: string, scriptUri: string, styleUri: string, cspSource: string): string {
  const attr = (value: string) => value.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
  return `<!doctype html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'none'; connect-src 'none'; style-src ${attr(cspSource)}; script-src 'nonce-${attr(nonce)}';">
<link rel="stylesheet" href="${attr(styleUri)}"><title>Token Agent</title></head>
<body><header><h1>Token Agent</h1><p id="status" role="status">Start a session to connect.</p>
<div class="toolbar"><button id="start">New session</button><button id="resume">Reconnect</button>
<button id="cancel" class="secondary">Cancel run</button><button id="report" class="secondary">Run report</button></div></header>
<main><section aria-label="Conversation"><div id="timeline" role="log" aria-live="polite"></div></section>
<section id="approvals" aria-label="Pending approvals"></section></main>
<form id="task-form"><label for="task">Repository task</label><textarea id="task" rows="3" maxlength="12000" required placeholder="Describe one task or ask about the repository."></textarea>
<div class="composer"><label><input id="research" type="checkbox"> Allow public documentation research</label>
<button id="send" type="submit">Send task</button></div></form>
<p class="hint">The core enforces policy and approvals. Reconnect restores the session; it does not repeat a task.</p>
<script nonce="${attr(nonce)}" src="${attr(scriptUri)}"></script></body></html>`;
}
