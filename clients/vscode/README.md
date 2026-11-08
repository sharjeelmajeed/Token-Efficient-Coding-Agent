# Token-Efficient Coding Agent for VS Code

A local-install extension for the confirmed hackathon project. Requires VS Code
1.144 or newer and the Python core from this package. It does not run a model itself.

1. Install `token-efficient-coding-agent-0.3.0.vsix` with **Extensions: Install from VSIX**.
2. Start the configured core and open its allowed repository in a trusted workspace.
3. Run **Token Agent: Configure Connection**, then **Token Agent: Start Session**.
4. Submit a task in chat. Review each native diff before selecting **Accept once**.
   Reject or reject with feedback to return control to the agent. Commands need an
   explicit confirmation of their complete argv and purpose.
5. Use **Reconnect Session**, **Cancel Run** or **Open Run Report** as needed.

The status bar displays the observed model, remaining budget and privacy mode.
API credentials use VS Code SecretStorage. Repository settings cannot replace the
trusted core origin. Provider credentials and action authorization stay in the core.
The core and workspace host must see the same absolute repository path; SSH/WSL
users run the core on that workspace host. This is not a multi-machine file sync tool.

Build with Node 22+ using `npm ci --ignore-scripts`, `npm test`, and `npm run package`.
`npm run test:host` downloads the pinned official VS Code test host and requires a
desktop display (Linux CI can use Xvfb). Host smoke tests do not replace manual diff,
approval, keyboard and theme acceptance described in `../../docs/M3_USAGE.md`.

No extension marketplace publication has occurred. Project source is private and
unlicensed for redistribution until its owners choose a license. Dependency licenses
are separate; see the root dependency review.
