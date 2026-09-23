# GoalEdge AI - install the pre-commit guard (PowerShell / Windows).
#
#     powershell -File qa/install-hooks.ps1
#
# Safe to re-run. Copies qa/pre-commit.ps1 to .git/hooks/pre-commit as a small
# shim so git (which invokes hooks with sh, even on Windows) delegates to it.
# If no git repository is present, it says so and exits cleanly rather than
# pretending to have installed something.

# Probe for git before calling it: with $ErrorActionPreference='Stop' a missing
# command is a terminating error, so a plain null-check would never run.
$repoRoot = $null
if (Get-Command git -ErrorAction SilentlyContinue) {
    try {
        $repoRoot = (& git rev-parse --show-toplevel 2>$null)
    } catch {
        $repoRoot = $null
    }
}

if (-not $repoRoot) {
    Write-Output "install-hooks: git unavailable or not inside a repository - nothing to install."
    Write-Output "install-hooks: the guard still runs via 'python qa/check-sources.py'."
    exit 0
}
$repoRoot = $repoRoot.Trim()

$ErrorActionPreference = "Stop"

$hooksDir = Join-Path $repoRoot ".git\hooks"
if (-not (Test-Path $hooksDir)) {
    Write-Output "install-hooks: $hooksDir not found - nothing to install."
    exit 1
}

$src = Join-Path $repoRoot "qa\pre-commit.ps1"
$shim = Join-Path $hooksDir "pre-commit"

# Git runs hooks through sh even on Windows, so the hook itself is a one-line
# shim that hands off to PowerShell. That keeps the real logic in one place,
# readable and testable, rather than duplicated in shell.
$content = @'
#!/bin/sh
# GoalEdge AI pre-commit shim -> delegates to qa/pre-commit.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File "$(git rev-parse --show-toplevel)/qa/pre-commit.ps1"
'@

Set-Content -Path $shim -Value $content -Encoding ascii -NoNewline
Write-Output "install-hooks: installed $shim"
Write-Output "install-hooks: the source guard now runs on every commit."
