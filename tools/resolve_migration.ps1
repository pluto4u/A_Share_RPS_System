param([Parameter(Mandatory=$true)][string]$Backup)
$ErrorActionPreference = 'Stop'
$planPath = Join-Path $Backup 'migration-plan.json'
$plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
$utf8 = [Text.UTF8Encoding]::new($false)
foreach ($item in $plan.files) {
    if ($item.state -ne 'conflict') { continue }
    $relative = $item.path
    $normalized = Join-Path $Backup 'normalized'
    $paths = @()
    foreach ($kind in @('target_before','base','incoming')) {
        $original = Join-Path (Join-Path $Backup $kind) $relative
        $dest = Join-Path (Join-Path $normalized $kind) $relative
        New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($dest)) -Force | Out-Null
        $content = [IO.File]::ReadAllText($original).Replace("`r`n", "`n")
        [IO.File]::WriteAllText($dest, $content, $utf8)
        $paths += $dest
    }
    $lines = & git merge-file -p -- $paths[0] $paths[1] $paths[2]
    $code = $LASTEXITCODE
    $staged = Join-Path (Join-Path $Backup 'merged') $relative
    [IO.File]::WriteAllText($staged, ($lines -join "`n") + "`n", $utf8)
    $item.state = if ($code -eq 0) { 'merged' } else { 'conflict' }
}
[IO.File]::WriteAllText($planPath, ($plan | ConvertTo-Json -Depth 8), $utf8)
$plan.files | Group-Object state | Select-Object Name,Count | Format-Table
$plan.files | Where-Object state -eq 'conflict' | Format-Table
