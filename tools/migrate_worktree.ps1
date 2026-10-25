param(
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$Target,
    [Parameter(Mandatory=$true)][string]$Backup
)
$ErrorActionPreference = 'Stop'
$sourceRoot = (Resolve-Path -LiteralPath $Source).Path
$targetRoot = (Resolve-Path -LiteralPath $Target).Path
$backupRoot = [IO.Path]::GetFullPath($Backup)
if ($sourceRoot -eq $targetRoot -or $backupRoot.StartsWith($sourceRoot + '\') -or $backupRoot.StartsWith($targetRoot + '\')) {
    throw 'Backup must be outside both source and target.'
}
if (Test-Path -LiteralPath $backupRoot) { throw 'Backup directory already exists.' }
New-Item -ItemType Directory -Path $backupRoot | Out-Null
$utf8 = [Text.UTF8Encoding]::new($false)
$baseRef = (& git -C $sourceRoot rev-parse HEAD).Trim()
$changed = @(& git -C $sourceRoot diff --name-only HEAD)
$new = @(& git -C $sourceRoot ls-files --others --exclude-standard)
$all = @(Get-ChildItem -LiteralPath $sourceRoot -Recurse -Force -File | Where-Object {
    $relative = $_.FullName.Substring($sourceRoot.Length + 1)
    $relative -ne '.git' -and $relative -notmatch '(^|\\)(__pycache__|\.pytest_cache|\.venv|venv)(\\|$)'
})
$manifest = @()
foreach ($file in $all) {
    $relative = $file.FullName.Substring($sourceRoot.Length + 1)
    $incoming = Join-Path (Join-Path $backupRoot 'incoming') $relative
    New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($incoming)) -Force | Out-Null
    Copy-Item -LiteralPath $file.FullName -Destination $incoming
    $existing = Join-Path $targetRoot $relative
    if (Test-Path -LiteralPath $existing -PathType Leaf) {
        $saved = Join-Path (Join-Path $backupRoot 'target_before') $relative
        New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($saved)) -Force | Out-Null
        Copy-Item -LiteralPath $existing -Destination $saved
    }
    $manifest += [pscustomobject]@{path=$relative; sha256=(Get-FileHash -LiteralPath $incoming -Algorithm SHA256).Hash}
}
$items = @()
foreach ($relative in @($changed + $new | Sort-Object -Unique)) {
    $incoming = Join-Path $sourceRoot $relative
    $existing = Join-Path $targetRoot $relative
    $staged = Join-Path (Join-Path $backupRoot 'merged') $relative
    New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($staged)) -Force | Out-Null
    $state = 'copy'
    if ((Test-Path -LiteralPath $existing -PathType Leaf) -and (Test-Path -LiteralPath $incoming -PathType Leaf)) {
        if ((Get-FileHash -LiteralPath $existing).Hash -eq (Get-FileHash -LiteralPath $incoming).Hash) {
            $state = 'identical'
        } elseif ($changed -contains $relative) {
            $base = Join-Path (Join-Path $backupRoot 'base') $relative
            New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($base)) -Force | Out-Null
            $baseLines = & git -C $sourceRoot show "${baseRef}:$relative"
            if ($LASTEXITCODE -ne 0) { throw "Cannot load base for $relative" }
            [IO.File]::WriteAllText($base, ($baseLines -join "`n") + "`n", $utf8)
            $mergedLines = & git merge-file -p -- $existing $base $incoming
            $mergeCode = $LASTEXITCODE
            [IO.File]::WriteAllText($staged, ($mergedLines -join "`n") + "`n", $utf8)
            $state = if ($mergeCode -eq 0) { 'merged' } else { 'conflict' }
        } else {
            $state = 'new-file-collision'
        }
    }
    if ($state -eq 'copy') { Copy-Item -LiteralPath $incoming -Destination $staged }
    $items += [pscustomobject]@{path=$relative; state=$state}
}
# Generated/ignored files are also part of the user's move request.
foreach ($file in $all) {
    $relative = $file.FullName.Substring($sourceRoot.Length + 1).Replace('\','/')
    if ($relative -like 'outputs/*' -or $relative -eq 'config/workspace.local.json') {
        if ($items.path -contains $relative) { continue }
        $staged = Join-Path (Join-Path $backupRoot 'merged') $relative
        New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($staged)) -Force | Out-Null
        $existing = Join-Path $targetRoot $relative
        if (Test-Path -LiteralPath $existing) {
            $state = if ((Get-FileHash -LiteralPath $existing).Hash -eq (Get-FileHash -LiteralPath $file.FullName).Hash) { 'identical' } else { 'new-file-collision' }
        } else {
            Copy-Item -LiteralPath $file.FullName -Destination $staged
            $state = 'copy'
        }
        $items += [pscustomobject]@{path=$relative; state=$state}
    }
}
$plan = [pscustomobject]@{source=$sourceRoot; target=$targetRoot; base=$baseRef; backup=$backupRoot; files=$items; source_manifest=$manifest}
[IO.File]::WriteAllText((Join-Path $backupRoot 'migration-plan.json'), ($plan | ConvertTo-Json -Depth 8), $utf8)
$items | Group-Object state | Select-Object Name,Count | Format-Table
$items | Where-Object state -In @('conflict','new-file-collision') | Format-Table
Write-Output "Prepared migration: $backupRoot"
