const path = require('node:path');
const fs = require('node:fs/promises');
const os = require('node:os');
const { runTests } = require('@vscode/test-electron');

async function main() {
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), 'token-agent-editor-'));
  try {
    await runTests({
      version: '1.144.0',
      extensionDevelopmentPath: path.resolve(__dirname, '..'),
      extensionTestsPath: path.join(__dirname, 'host-suite.cjs'),
      launchArgs: [folder, '--disable-extensions', '--disable-workspace-trust',
        '--skip-welcome', '--skip-release-notes', '--user-data-dir', path.join(folder, 'profile')],
    });
  } finally { await fs.rm(folder, { recursive: true, force: true }); }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
