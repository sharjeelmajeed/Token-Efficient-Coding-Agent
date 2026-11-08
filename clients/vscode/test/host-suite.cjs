const assert = require('node:assert/strict');
const vscode = require('vscode');

exports.run = async function () {
  const extension = vscode.extensions.getExtension('token-agent-hackathon.token-efficient-coding-agent');
  assert.ok(extension, 'Development extension must be installed in the test host');
  await extension.activate();
  assert.ok(extension.isActive);
  const commands = await vscode.commands.getCommands(true);
  for (const name of ['configure', 'start', 'chat', 'resume', 'cancel', 'report']) {
    assert.ok(commands.includes(`tokenAgent.${name}`));
  }
  const uri = vscode.Uri.parse('token-agent:/test/expired-preview');
  const document = await vscode.workspace.openTextDocument(uri);
  assert.match(document.getText(), /preview expired/);
  await assert.rejects(vscode.workspace.fs.writeFile(uri, Buffer.from('unapproved edit')));
  await vscode.commands.executeCommand('tokenAgent.chat');
  console.log('Extension host: activation, command registration, virtual document and chat smoke passed.');
};
