# GoalEdge AI - pre-commit guard (PowerShell).
#
# Blocks a commit that contains a damaged Python source: a collapsed docstring
# delimiter, or any other syntax error, printing the exact file and line.
#
# This is the Windows-native twin of qa/pre-commit. Use whichever matches the
# shell that runs on your clone:
#
#     # POSIX clone (Linux / macOS / Git Bash)
#     sh qa/install-hooks.sh
#
#     # Windows clone (this file)
#     powershell -File qa/install-hooks.ps1
#
# Run the check directly at any time:
#     powershell -File qa/pre-commit.ps1

# Resolve the repo root defensively. `git` may be absent entirely, and with
# $ErrorActionPreference='Stop' a missing command throws a *terminating* error
# that a plain null-check never sees. Probing for the command first, then
# wrapping the call, keeps "no git here" a clean no-op instead of a crash.
$repoRoot = $null
if (Get-Command git -ErrorAction SilentlyContinue) {
    try {
        $repoRoot = (& git rev-parse --show-toplevel 2>$null)
    } catch {
        $repoRoot = $null
    }
}

if (-not $repoRoot) {
    Write-Output "pre-commit: git unavailable or not inside a repository - skipping the source guard."
    exit 0
}
$repoRoot = $repoRoot.Trim()

$ErrorActionPreference = "Stop"

$python = Join-Path $repoRoot "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

Push-Location $repoRoot
try {
    & $python "qa\check-sources.py"
    $status = $LASTEXITCODE
} finally {
    Pop-Location
}

if ($status -ne 0) {
    Write-Output ""
    Write-Output "pre-commit: blocked - fix the sources above before committing."
    Write-Output "pre-commit: repair collapsed docstrings with: python qa/fix_docstrings.py"
    exit $status
}

exit 0
