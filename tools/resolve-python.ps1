<#
.SYNOPSIS
  envcloak: locate a working Python 3 interpreter and bake it into Claude Code
  settings so the MCP server and PreToolUse hook can resolve it.

.DESCRIPTION
  MANUAL fallback for native Windows machines that have NO POSIX shell (no Git
  Bash / MSYS2). On every other platform the SessionStart hook runs
  tools/resolve-python.sh automatically; this script is NOT wired as a hook,
  because a PowerShell hook entry would emit a visible "command not found"
  error on every macOS/Linux session.

  Run it once from PowerShell, then restart Claude Code:
      powershell -NoProfile -ExecutionPolicy Bypass -File tools\resolve-python.ps1

  It finds ANY interpreter that runs as Python >= 3.8 (verifying by EXECUTING
  it -- the Windows Store "python3"/"python" aliases resolve but exit without
  running), then hands off to resolve_interpreter.py, which records the
  canonical sys.executable into ~/.claude/settings.json -> env.ENVCLOAK_PYTHON.
#>

$ErrorActionPreference = 'SilentlyContinue'

$root = $env:CLAUDE_PLUGIN_ROOT
if (-not $root) { $root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
$brain = Join-Path $root 'tools\resolve_interpreter.py'

function Test-Interp {
    param([string]$Exe, [string[]]$Pre)
    try {
        $a = @()
        if ($Pre) { $a += $Pre }
        $a += @('-c', 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,8) else 1)')
        & $Exe @a 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

# Candidate (exe, prefix-args), override first, then names, then the py launcher.
$cands = New-Object System.Collections.ArrayList
if ($env:ENVCLOAK_PYTHON) { [void]$cands.Add(@($env:ENVCLOAK_PYTHON, @())) }
foreach ($n in 'python3','python','python3.13','python3.12','python3.11','python3.10','python3.9','python3.8') {
    [void]$cands.Add(@($n, @()))
}
[void]$cands.Add(@('py', @('-3')))

foreach ($c in $cands) {
    $exe = $c[0]; $pre = $c[1]
    if (Test-Interp -Exe $exe -Pre $pre) {
        & $exe @pre $brain | Out-Null
        exit 0
    }
}

Write-Error "[envcloak] no working Python 3.8+ found. Set ENVCLOAK_PYTHON to your interpreter and restart Claude Code."
exit 0
