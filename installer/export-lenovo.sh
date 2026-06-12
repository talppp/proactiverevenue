#!/usr/bin/env bash
# export-lenovo.sh — same as export-lenovo.ps1 but for WSL / Linux on the
# Lenovo. Produces ~/lenovo-export.zip for setup-mac-mini.sh.
set -uo pipefail

OUT="$HOME/lenovo-export"
SCAN_ROOTS=("$HOME/dev" "$HOME/source" "$HOME/repos" "$HOME/projects" "$HOME/Documents" "$HOME")
rm -rf "$OUT" && mkdir -p "$OUT"
step() { printf '\033[36m==> %s\033[0m\n' "$*"; }

# 1. Claude Code settings
step "Claude Code settings"
if [ -d "$HOME/.claude" ]; then
  rsync -a \
    --exclude='.credentials.json' --exclude='cache/' --exclude='statsig/' \
    --exclude='shell-snapshots/' --exclude='todos/' --exclude='downloads/' \
    "$HOME/.claude/" "$OUT/claude-home/"
fi
[ -f "$HOME/.claude.json" ] && cp "$HOME/.claude.json" "$OUT/claude.json"
echo "WINDOWS_HOME=$HOME" > "$OUT/source-machine.txt"

# 2. Claude Desktop config (Linux path; WSL users: Desktop config lives on
#    the Windows side — run the .ps1 for that file if needed)
[ -f "$HOME/.config/Claude/claude_desktop_config.json" ] && \
  cp "$HOME/.config/Claude/claude_desktop_config.json" "$OUT/"

# 3. Git repositories
step "Scanning for git repositories"
: > "$OUT/repos.txt"
for root in "${SCAN_ROOTS[@]}"; do
  [ -d "$root" ] || continue
  find "$root" -maxdepth 5 -name .git -type d 2>/dev/null | while read -r g; do
    repo=$(dirname "$g")
    url=$(git -C "$repo" remote get-url origin 2>/dev/null) || continue
    branch=$(git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null)
    rel=${repo#"$HOME"/}
    printf '%s\t%s\t%s\n' "$url" "$branch" "$rel" >> "$OUT/repos.txt"
    echo "    $rel ($branch)"
  done
done
sort -u "$OUT/repos.txt" -o "$OUT/repos.txt"

# 4. Installed packages (apt, if present)
command -v apt-mark >/dev/null && apt-mark showmanual > "$OUT/apt-manual.txt" 2>/dev/null

# 5. VS Code extensions
command -v code >/dev/null && code --list-extensions > "$OUT/vscode-extensions.txt" 2>/dev/null

# 6. npm globals
if command -v npm >/dev/null; then
  npm ls -g --depth=0 --parseable 2>/dev/null | tail -n +2 | xargs -rn1 basename \
    | grep -vE '^(npm|corepack)$' > "$OUT/npm-globals.txt"
fi

# 7. Python packages
command -v pip3 >/dev/null && pip3 freeze > "$OUT/pip-freeze.txt" 2>/dev/null

# 8. Ollama models
command -v ollama >/dev/null && ollama list 2>/dev/null | tail -n +2 | awk '{print $1}' > "$OUT/ollama-models.txt"

# 9. Versions
{
  echo "node:   $(node -v 2>/dev/null)"
  echo "python: $(python3 --version 2>/dev/null)"
  echo "git:    $(git --version 2>/dev/null)"
  echo "claude: $(claude --version 2>/dev/null)"
} > "$OUT/versions.txt"

# 10. Zip
step "Creating zip"
(cd "$OUT" && zip -qr "$HOME/lenovo-export.zip" .)
echo
printf '\033[32mDONE: %s\033[0m\n' "$HOME/lenovo-export.zip"
echo "Transfer PRIVATELY to the Mac, then: ./setup-mac-mini.sh ~/Desktop/lenovo-export.zip"
echo "NEVER commit this zip to git — it may contain API keys."
