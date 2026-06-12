# Lenovo → Mac mini migration kit (Claude Code + full dev environment)

This kit clones your entire Lenovo development setup onto a Mac mini:
Claude Code, Claude Desktop, all prerequisites, third-party apps
(Ollama, Puppeteer, Python, Visual Studio Code, …), your repositories,
and all your Claude settings / `.md` memory files — so the Mac looks
exactly like the Lenovo.

> **Two clarifications before you start**
>
> 1. **macOS, not iOS.** Claude Code runs on macOS (the Mac mini), not on
>    iOS. iPhones/iPads can only use the Claude mobile app from the App
>    Store — settings there sync through your Anthropic account
>    automatically.
> 2. **Why two steps?** Your settings and app list live *on the Lenovo's
>    disk*. Nothing in the cloud knows what's installed there, so step 1
>    exports a snapshot from the Lenovo, and step 2 replays it on the Mac.

## Step 1 — Export from the Lenovo (run once, on the Lenovo)

**Windows (PowerShell):**

```powershell
# In PowerShell (regular user is fine):
Set-ExecutionPolicy -Scope Process Bypass -Force
.\export-lenovo.ps1
```

**WSL / Linux on the Lenovo instead?** Run `./export-lenovo.sh`.

Either script produces **`lenovo-export.zip`** on your Desktop (or home
directory) containing:

| File | Contents |
|---|---|
| `claude-home/` | Your `~/.claude/` folder — `settings.json`, `CLAUDE.md`, agents, skills, commands, hooks, keybindings |
| `claude.json` | Global Claude Code config (`~/.claude.json`) incl. MCP servers |
| `claude_desktop_config.json` | Claude Desktop app config (MCP servers etc.) |
| `repos.txt` | Every git repo found on the machine: remote URL, branch, relative path |
| `winget-packages.json` / `winget-ids.txt` | Every app installed via Windows package manager |
| `vscode-extensions.txt` | All VS Code extensions |
| `npm-globals.txt` | Globally installed npm packages (incl. puppeteer) |
| `pip-freeze.txt` | Python packages |
| `ollama-models.txt` | Ollama models to re-pull |
| `versions.txt` | Tool versions for reference |

## Step 2 — Transfer the zip to the Mac mini

AirDrop, USB stick, iCloud Drive — anything **private**.

> ⚠️ **Do NOT commit `lenovo-export.zip` to GitHub or upload it
> publicly.** `claude.json` and MCP configs can contain API keys and
> tokens. A `.gitignore` in this folder blocks it as a safety net.

## Step 3 — Run the installer on the Mac mini

```bash
# Get the kit onto the Mac (Safari: download this repo as zip, or):
git clone https://github.com/talppp/proactiverevenue.git
cd proactiverevenue/installer

# Put lenovo-export.zip in this folder (or pass its path), then:
chmod +x setup-mac-mini.sh
./setup-mac-mini.sh ~/Desktop/lenovo-export.zip
```

The installer runs **in sequence**:

1. Xcode Command Line Tools
2. Homebrew
3. Core tools: git, jq, node (LTS), python 3.12
4. Apps: Visual Studio Code, Ollama, Google Chrome, Claude Desktop
5. Claude Code (native installer, npm fallback)
6. Puppeteer (global npm install — downloads its own Chrome for Testing)
7. **Restore from the Lenovo export:**
   - `~/.claude/` settings, `CLAUDE.md`, agents, skills, hooks
   - `~/.claude.json` (Windows paths auto-rewritten to Mac paths)
   - Claude Desktop config → `~/Library/Application Support/Claude/`
   - VS Code extensions reinstalled one by one
   - npm globals reinstalled
   - Python packages reinstalled
   - Ollama models re-pulled
   - **All repos re-cloned** into `~/dev/…` on their original branches
   - Remaining winget apps mapped to Homebrew where possible; anything
     unmapped is printed for manual install
8. Summary report

The script is **idempotent** — safe to re-run if anything fails partway.

## Step 4 — Sign in (the only manual part)

Credentials are deliberately *not* copied (security):

```bash
claude            # then /login — authorize with your Anthropic account
```

Also sign in to Claude Desktop, VS Code (Settings Sync, if you use it),
and `git` will prompt for GitHub auth on first push
(`brew install gh && gh auth login` is the easiest).

After that, open any repo and run `claude` — same settings, same
memory files, same MCP servers, same everything.
