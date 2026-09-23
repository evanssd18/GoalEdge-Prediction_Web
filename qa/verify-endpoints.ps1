# Verify the UI-facing endpoints actually return the synced matches.
$ErrorActionPreference = "Stop"

foreach ($day in @("today", "tomorrow")) {
    $ls = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/livescores?date=$day" -TimeoutSec 60
    Write-Output "LIVESCORES $day : source=$($ls.source) total=$($ls.total) live=$($ls.live_count) groups=$($ls.groups.Count) truncated=$($ls.truncated)"
}

$t = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/tips?limit=25" -TimeoutSec 120
Write-Output ""
Write-Output "TIPS: count=$($t.tips.Count)"
foreach ($tip in ($t.tips | Select-Object -First 10)) {
    Write-Output ("  {0} {1} v {2} | tip={3} prob={4} odds={5} src={6}" -f `
        $tip.kickoff, $tip.home_team, $tip.away_team, $tip.tip, $tip.probability, $tip.best_odds, $tip.source)
}

$vb = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/value-bets?limit=10" -TimeoutSec 120
Write-Output ""
Write-Output "VALUE BETS: count=$($vb.bets.Count)"
foreach ($b in ($vb.bets | Select-Object -First 6)) {
    Write-Output ("  {0} v {1} | {2} edge={3} odds={4}" -f $b.home_team, $b.away_team, $b.tip, $b.edge, $b.best_odds)
}
