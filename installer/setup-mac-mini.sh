#!/usr/bin/env bash
# setup-mac-mini.sh — turn a fresh Mac mini into a clone of the Lenovo dev
# environment: Claude Code + Claude Desktop + prerequisites + third-party
# apps + all repos + all Claude settings/memory files.
#
# Usage:
#   ./setup-mac-mini.sh [path/to/lenovo-export.zip | path/to/lenovo-export/]
#
# Idempotent: safe to re-run after a failure; completed steps are skipped.
set -uo pipefail

BUNDLE_ARG="${1:-}"
DEV_ROOT="$HOME/dev"          # repos restored under here
LOG="$HOME/mac-mini-setup.log"
FAILURES=()

c() { printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
step() { echo; c "1;36" "==> $*"; echo "[$(date '+%H:%M:%S')] $*" >> "$LOG"; }
ok()   { c "32" "    ✔ $*"; }
warn() { c "33" "    ⚠ $*"; FAILURES+=("$*"); }

[ "$(uname -s)" = "Darwin" ] || { c "31" "This script must run on macOS (the Mac mini)."; exit 1; }

# ---------------------------------------------------------------- 1. Xcode CLT
step "1/9 Xcode Command Line Tools"
if xcode-select -p >/dev/null 2>&1; then ok "already installed"
else
  xcode-select --install 2>/dev/null || true
  c "33" "    A dialog should appear — click Install, then this script waits."
  until xcode-select -p >/dev/null 2>&1; do sleep 10; done
  ok "installed"
fi

# ---------------------------------------------------------------- 2. Homebrew
step "2/9 Homebrew"
if ! command -v brew >/dev/null 2>&1; then
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
# Apple Silicon vs Intel path
for B in /opt/homebrew/bin/brew /usr/local/bin/brew; do
  [ -x "$B" ] && eval "$("$B" shellenv)" && break
done
grep -q 'brew shellenv' "$HOME/.zprofile" 2>/dev/null || \
  echo "eval \"\$($(command -v brew) shellenv)\"" >> "$HOME/.zprofile"
ok "$(brew --version | head -1)"

# ----------------------------------------------------- 3. Core CLI tools
step "3/9 Core tools (git, jq, node, python)"
for f in git jq node python@3.12; do
  brew list "$f" >/dev/null 2>&1 || brew install "$f" || warn "brew install $f failed"
done
ok "node $(node -v 2>/dev/null), $(python3 --version 2>/dev/null)"

# ----------------------------------------------------- 4. GUI apps (casks)
step "4/9 Apps: VS Code, Ollama, Chrome, Claude Desktop"
for cask in visual-studio-code ollama google-chrome claude; do
  if brew list --cask "$cask" >/dev/null 2>&1; then ok "$cask already installed"
  else brew install --cask "$cask" && ok "$cask" || warn "cask $cask failed (may already exist in /Applications)"
  fi
done
# Make `code` CLI available
[ -x "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code" ] && \
  export PATH="$PATH:/Applications/Visual Studio Code.app/Contents/Resources/app/bin"
# Start Ollama so models can be pulled later
open -a Ollama 2>/dev/null || true

# ----------------------------------------------------- 5. Claude Code
step "5/9 Claude Code"
if command -v claude >/dev/null 2>&1; then ok "already installed: $(claude --version 2>/dev/null)"
else
  curl -fsSL https://claude.ai/install.sh | bash || npm install -g @anthropic-ai/claude-code || warn "Claude Code install failed"
  export PATH="$HOME/.local/bin:$PATH"
  command -v claude >/dev/null 2>&1 && ok "$(claude --version 2>/dev/null)"
fi

# ----------------------------------------------------- 6. Puppeteer
step "6/9 Puppeteer"
if npm ls -g puppeteer >/dev/null 2>&1; then ok "already installed"
else npm install -g puppeteer && ok "puppeteer (+ Chrome for Testing)" || warn "puppeteer install failed"
fi

# ----------------------------------------------------- 7. Locate export bundle
step "7/9 Lenovo export bundle"
BUNDLE=""
if [ -n "$BUNDLE_ARG" ]; then BUNDLE="$BUNDLE_ARG"
else
  for guess in ./lenovo-export.zip ./lenovo-export "$HOME/Desktop/lenovo-export.zip" "$HOME/Downloads/lenovo-export.zip"; do
    [ -e "$guess" ] && BUNDLE="$guess" && break
  done
fi
if [ -z "$BUNDLE" ]; then
  warn "No lenovo-export bundle found — skipping settings/repo restore. Re-run with: ./setup-mac-mini.sh path/to/lenovo-export.zip"
else
  case "$BUNDLE" in
    *.zip) EX="$(mktemp -d)"; unzip -qo "$BUNDLE" -d "$EX";;
    *)     EX="$BUNDLE";;
  esac
  ok "using bundle: $BUNDLE"

  # ---- 7a. Claude settings (~/.claude) ----
  if [ -d "$EX/claude-home" ]; then
    mkdir -p "$HOME/.claude"
    rsync -a "$EX/claude-home/" "$HOME/.claude/"
    ok "restored ~/.claude (settings.json, CLAUDE.md, agents, skills, hooks)"
  fi

  # ---- 7b. ~/.claude.json with Windows→Mac path rewrite ----
  if [ -f "$EX/claude.json" ]; then
    WIN_HOME="$(sed -n 's/^WINDOWS_HOME=//p' "$EX/source-machine.txt" 2>/dev/null | tr -d '\r')"
    [ -f "$HOME/.claude.json" ] && cp "$HOME/.claude.json" "$HOME/.claude.json.bak"
    python3 - "$EX/claude.json" "$WIN_HOME" "$HOME" <<'PY'
import json, re, sys
src, win_home, mac_home = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(src, encoding="utf-8-sig").read()
if win_home:
    for v in (win_home, win_home.replace("/", "\\\\"), win_home.replace("/", "\\")):
        text = text.replace(v, mac_home)
text = re.sub(r'[A-Za-z]:\\\\Users\\\\[^\\\\"]+', mac_home.replace("\\", ""), text)
data = json.loads(text)
# normalize leftover backslashes in any rewritten path (keys and values)
def fix(o):
    if isinstance(o, dict):
        return {(k.replace("\\", "/") if mac_home in k else k): fix(v) for k, v in o.items()}
    if isinstance(o, list):
        return [fix(v) for v in o]
    if isinstance(o, str) and mac_home in o:
        return o.replace("\\", "/")
    return o
json.dump(fix(data), open(mac_home + "/.claude.json", "w"), indent=2)
PY
    ok "restored ~/.claude.json (paths rewritten for macOS)"
  fi

  # ---- 7c. Claude Desktop config ----
  if [ -f "$EX/claude_desktop_config.json" ]; then
    mkdir -p "$HOME/Library/Application Support/Claude"
    cp "$EX/claude_desktop_config.json" "$HOME/Library/Application Support/Claude/"
    ok "restored Claude Desktop config (review MCP server paths inside it)"
  fi

  # ---- 7d. VS Code extensions ----
  if [ -f "$EX/vscode-extensions.txt" ] && command -v code >/dev/null 2>&1; then
    while IFS= read -r ext; do
      ext="$(echo "$ext" | tr -d '\r')"; [ -z "$ext" ] && continue
      code --install-extension "$ext" --force >/dev/null 2>&1 && echo "    + $ext" || warn "vscode ext $ext failed"
    done < "$EX/vscode-extensions.txt"
    ok "VS Code extensions restored"
  fi

  # ---- 7e. npm globals ----
  if [ -f "$EX/npm-globals.txt" ]; then
    while IFS= read -r pkg; do
      pkg="$(echo "$pkg" | tr -d '\r')"; [ -z "$pkg" ] && continue
      npm ls -g "$pkg" >/dev/null 2>&1 || npm install -g "$pkg" >/dev/null 2>&1 \
        && echo "    + $pkg" || warn "npm -g $pkg failed"
    done < "$EX/npm-globals.txt"
  fi

  # ---- 7f. Python packages ----
  if [ -f "$EX/pip-freeze.txt" ]; then
    python3 -m pip install --user --break-system-packages -r "$EX/pip-freeze.txt" >/dev/null 2>&1 \
      && ok "python packages restored" \
      || warn "some pip packages failed — see $EX/pip-freeze.txt (per-project venvs are recreated by each repo's own setup anyway)"
  fi

  # ---- 7g. Repositories ----
  if [ -f "$EX/repos.txt" ]; then
    mkdir -p "$DEV_ROOT"
    while IFS=$'\t' read -r url branch rel; do
      url="$(echo "$url" | tr -d '\r')"; rel="$(echo "$rel" | tr -d '\r')"; branch="$(echo "$branch" | tr -d '\r')"
      [ -z "$url" ] && continue
      dest="$DEV_ROOT/$(basename "$rel")"
      if [ -d "$dest/.git" ]; then echo "    = $(basename "$rel") (already cloned)"
      else
        git clone "$url" "$dest" >/dev/null 2>&1 \
          && { [ -n "$branch" ] && git -C "$dest" checkout "$branch" >/dev/null 2>&1; echo "    + $(basename "$rel") @ $branch"; } \
          || warn "clone failed: $url (private repo? run 'gh auth login' then re-run this script)"
      fi
    done < "$EX/repos.txt"
    ok "repositories restored under $DEV_ROOT"
  fi

  # ---- 7h. Ollama models ----
  if [ -f "$EX/ollama-models.txt" ] && command -v ollama >/dev/null 2>&1; then
    while IFS= read -r model; do
      model="$(echo "$model" | tr -d '\r')"; [ -z "$model" ] && continue
      ollama pull "$model" && echo "    + $model" || warn "ollama pull $model failed"
    done < "$EX/ollama-models.txt"
  fi

  # ---- 7i. Remaining winget apps → Homebrew ----
  if [ -f "$EX/winget-ids.txt" ]; then
    step "8/9 Mapping remaining Lenovo apps to Homebrew"
    UNMAPPED=()
    while IFS= read -r id; do
      id="$(echo "$id" | tr -d '\r')"; [ -z "$id" ] && continue
      cask=""
      case "$id" in
        # already handled above or built into macOS
        Microsoft.VisualStudioCode|Ollama.Ollama|Anthropic.Claude*|Anthropic.ClaudeCode|\
        Git.Git|OpenJS.NodeJS*|Python.Python*|Google.Chrome|Microsoft.WindowsTerminal|\
        Microsoft.PowerShell|Microsoft.Edge*|Microsoft.VCRedist*|Microsoft.DotNet*|\
        Microsoft.WindowsSDK*|NVIDIA.*|Intel.*|Lenovo.*) continue;;
        Docker.DockerDesktop)        cask="docker-desktop";;
        Microsoft.VisualStudio*)     cask="visual-studio-code";;  # VS proper has no Mac equiv; VS Code covers it
        SlackTechnologies.Slack)     cask="slack";;
        Discord.Discord)             cask="discord";;
        Zoom.Zoom)                   cask="zoom";;
        Notion.Notion)               cask="notion";;
        Spotify.Spotify)             cask="spotify";;
        Mozilla.Firefox)             cask="firefox";;
        Brave.Brave)                 cask="brave-browser";;
        Postman.Postman)             cask="postman";;
        WhatsApp.WhatsApp)           cask="whatsapp";;
        Telegram.TelegramDesktop)    cask="telegram";;
        OpenAI.ChatGPT)              cask="chatgpt";;
        GitHub.GitHubDesktop)        cask="github";;
        GitHub.cli)                  brew install gh >/dev/null 2>&1 && echo "    + gh"; continue;;
        7zip.7zip)                   brew install p7zip >/dev/null 2>&1 && echo "    + p7zip"; continue;;
        *) UNMAPPED+=("$id"); continue;;
      esac
      brew list --cask "$cask" >/dev/null 2>&1 || brew install --cask "$cask" >/dev/null 2>&1 \
        && echo "    + $cask" || warn "cask $cask failed"
    done < "$EX/winget-ids.txt"
    if [ "${#UNMAPPED[@]}" -gt 0 ]; then
      c "33" "    Apps with no automatic Mac equivalent (install manually if needed):"
      printf '      - %s\n' "${UNMAPPED[@]}"
    fi
  fi
fi

# ---------------------------------------------------------------- 9. Summary
step "9/9 Summary"
echo "    Claude Code:    $(command -v claude >/dev/null && claude --version 2>/dev/null || echo MISSING)"
echo "    Claude Desktop: $([ -d '/Applications/Claude.app' ] && echo installed || echo MISSING)"
echo "    VS Code:        $([ -d '/Applications/Visual Studio Code.app' ] && echo installed || echo MISSING)"
echo "    Ollama:         $(command -v ollama >/dev/null && ollama --version 2>/dev/null || echo MISSING)"
echo "    Node/Python:    $(node -v 2>/dev/null) / $(python3 --version 2>/dev/null)"
echo "    Repos:          $(ls -d "$DEV_ROOT"/*/ 2>/dev/null | wc -l | tr -d ' ') under $DEV_ROOT"
if [ "${#FAILURES[@]}" -gt 0 ]; then
  echo; c "33" "    Items needing attention:"; printf '      - %s\n' "${FAILURES[@]}"
fi
echo
c "1;32" "Done. Final manual steps:"
echo "  1. claude          → /login (your Anthropic account)"
echo "  2. Open Claude Desktop and sign in"
echo "  3. brew install gh && gh auth login   (GitHub auth for private repos/pushes)"
echo "  4. Open a repo and run 'claude' — your CLAUDE.md, settings, and MCP servers are all there."
