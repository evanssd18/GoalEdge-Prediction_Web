# File: qa/probe-serving.ps1
# Is the app already being served on 8080, and is it the current frontend?
$ErrorActionPreference = "Stop"
try {
  $r = Invoke-WebRequest -Uri "http://127.0.0.1:8080/" -UseBasicParsing -TimeoutSec 10
  Write-Output ("STATUS: " + $r.StatusCode)
  if ($r.Content -match "GoalEdge") { Write-Output "TITLE: GoalEdge present" }

  $js = Invoke-WebRequest -Uri "http://127.0.0.1:8080/app.js" -UseBasicParsing -TimeoutSec 10
  Write-Output ("APP.JS STATUS: " + $js.StatusCode)
  # Our new helper must be in the bytes the browser actually receives.
  if ($js.Content -match "h2hResultsHTML") {
    Write-Output "HELPER PRESENT: h2hResultsHTML is served"
  } else {
    Write-Output "HELPER MISSING: server is serving a stale app.js"
  }
  if ($js.Content -match "Head-to-head results") {
    Write-Output "HEADING PRESENT: 'Head-to-head results' is served"
  } else {
    Write-Output "HEADING MISSING"
  }
} catch {
  Write-Output ("ERR: " + $_.Exception.Message)
}
