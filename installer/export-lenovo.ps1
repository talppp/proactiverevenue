# export-lenovo.ps1 — snapshot this Windows machine's dev environment so it
# can be replayed on a Mac by setup-mac-mini.sh.
#
# Usage:  Set-ExecutionPolicy -Scope Process Bypass -Force
#         .\export-lenovo.ps1 [-ScanRoots <dirs to search for git repos>]
#
# Output: Desktop\lenovo-export.zip   (transfer it PRIVATELY — it can
#         contain API keys inside claude.json / MCP configs)

param(
    [string[]]$ScanRoots = @(
        "$env:USERPROFILE\dev",
        "$env:USERPROFILE\source",
        "$env:USERPROFILE\repos",
        "$env:USERPROFILE\projects",
        "$env:USERPROFILE\Documents\GitHub",
        "$env:USERPROFILE\Documents",
        "$env:USERPROFILE\Desktop"
    )
)

$ErrorActionPreference = "Continue"
$out = Join-Path $env:USERPROFILE "Desktop\lenovo-export"
if (Test-Path $out) { Remove-Item $out -Recurse -Force }
New-Item -ItemType Directory -Path $out | Out-Null
Write-Host "==> Exporting to $out"

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

# --- 1. Claude Code settings (~/.claude and ~/.claude.json) ---------------
Step "Claude Code settings"
$claudeHome = Join-Path $env:USERPROFILE ".claude"
if (Test-Path $claudeHome) {
    # Exclude credentials (you re-login on the Mac) and bulky caches.
    robocopy $claudeHome (Join-Path $out "claude-home") /E `
        /XF ".credentials.json" "*.lock" `
        /XD "cache" "Cache" "downloads" "statsig" "shell-snapshots" "todos" `
        /NFL /NDL /NJH /NJS | Out-Null
    Write-Host "    copied ~/.claude"
} else { Write-Host "    ~/.claude not found — skipped" }

$claudeJson = Join-Path $env:USERPROFILE ".claude.json"
if (Test-Path $claudeJson) {
    Copy-Item $claudeJson (Join-Path $out "claude.json")
    Write-Host "    copied ~/.claude.json (contains MCP servers — keep private!)"
}

# Note original Windows home so the Mac script can rewrite paths.
"WINDOWS_HOME=$($env:USERPROFILE -replace '\\','/')" |
    Out-File (Join-Path $out "source-machine.txt") -Encoding utf8

# --- 2. Claude Desktop config ---------------------------------------------
Step "Claude Desktop config"
$desktopCfg = Join-Path $env:APPDATA "Claude\claude_desktop_config.json"
if (Test-Path $desktopCfg) {
    Copy-Item $desktopCfg (Join-Path $out "claude_desktop_config.json")
    Write-Host "    copied claude_desktop_config.json"
} else { Write-Host "    Claude Desktop config not found — skipped" }

# --- 3. Git repositories ----------------------------------------------------
Step "Scanning for git repositories (this can take a minute)"
$repoLines = @()
foreach ($root in $ScanRoots) {
    if (-not (Test-Path $root)) { continue }
    Get-ChildItem -Path $root -Directory -Recurse -Depth 4 -Force -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -eq ".git" } |
      ForEach-Object {
        $repo = $_.Parent.FullName
        $url    = git -C $repo remote get-url origin 2>$null
        $branch = git -C $repo rev-parse --abbrev-ref HEAD 2>$null
        if ($url) {
            $rel = $repo.Replace("$env:USERPROFILE\", "").Replace("\", "/")
            $repoLines += "$url`t$branch`t$rel"
            Write-Host "    $rel  ($branch)"
        }
      }
}
$repoLines | Sort-Object -Unique | Out-File (Join-Path $out "repos.txt") -Encoding utf8

# --- 4. Installed applications (winget) ------------------------------------
Step "Installed applications"
winget export -o (Join-Path $out "winget-packages.json") --accept-source-agreements 2>$null | Out-Null
if (Test-Path (Join-Path $out "winget-packages.json")) {
    $json = Get-Content (Join-Path $out "winget-packages.json") -Raw | ConvertFrom-Json
    $json.Sources.Packages.PackageIdentifier | Sort-Object -Unique |
        Out-File (Join-Path $out "winget-ids.txt") -Encoding utf8
    Write-Host "    exported winget package list"
}

# --- 5. VS Code extensions ---------------------------------------------------
Step "VS Code extensions"
$code = Get-Command code -ErrorAction SilentlyContinue
if ($code) {
    code --list-extensions | Out-File (Join-Path $out "vscode-extensions.txt") -Encoding utf8
    Write-Host "    $(@(Get-Content (Join-Path $out 'vscode-extensions.txt')).Count) extensions"
}

# --- 6. npm globals (puppeteer etc.) ----------------------------------------
Step "Global npm packages"
$npm = Get-Command npm -ErrorAction SilentlyContinue
if ($npm) {
    $g = npm ls -g --depth=0 --json 2>$null | ConvertFrom-Json
    $g.dependencies.PSObject.Properties.Name |
        Where-Object { $_ -notin @("npm", "corepack") } |
        Out-File (Join-Path $out "npm-globals.txt") -Encoding utf8
}

# --- 7. Python packages -------------------------------------------------------
Step "Python packages"
$pip = Get-Command pip -ErrorAction SilentlyContinue
if ($pip) { pip freeze 2>$null | Out-File (Join-Path $out "pip-freeze.txt") -Encoding utf8 }

# --- 8. Ollama models ----------------------------------------------------------
Step "Ollama models"
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollama) {
    (ollama list 2>$null | Select-Object -Skip 1) |
        ForEach-Object { ($_ -split '\s+')[0] } | Where-Object { $_ } |
        Out-File (Join-Path $out "ollama-models.txt") -Encoding utf8
}

# --- 9. Tool versions (reference) ----------------------------------------------
Step "Tool versions"
@(
    "node:   $(node -v 2>$null)"
    "npm:    $(npm -v 2>$null)"
    "python: $(python --version 2>$null)"
    "git:    $(git --version 2>$null)"
    "claude: $(claude --version 2>$null)"
) | Out-File (Join-Path $out "versions.txt") -Encoding utf8

# --- 10. Zip it ------------------------------------------------------------------
Step "Creating zip"
$zip = Join-Path $env:USERPROFILE "Desktop\lenovo-export.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path "$out\*" -DestinationPath $zip
Write-Host ""
Write-Host "DONE: $zip" -ForegroundColor Green
Write-Host "Transfer it PRIVATELY (AirDrop/USB) to the Mac mini, then run:"
Write-Host "    ./setup-mac-mini.sh ~/Desktop/lenovo-export.zip"
Write-Host "NEVER commit this zip to git — it may contain API keys." -ForegroundColor Yellow
