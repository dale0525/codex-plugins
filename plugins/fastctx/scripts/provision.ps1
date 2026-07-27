$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$pluginRoot = Split-Path -Parent $PSScriptRoot
$metadataPath = Join-Path $pluginRoot 'upstream-release.json'
$action = if ($args.Count -gt 0) { $args[0] } else { 'status' }
$metadata = Get-Content -LiteralPath $metadataPath -Raw -Encoding UTF8 | ConvertFrom-Json
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
$fastctxDirectory = Join-Path $env:USERPROFILE '.fastctx'
$fastctxConfig = Join-Path $fastctxDirectory 'config.toml'
$stableBinary = Join-Path $fastctxDirectory 'bin\fastctx.exe'

function Test-FastShellEnabled {
    if (-not (Test-Path -LiteralPath $fastctxConfig)) { return $false }
    $lines = Get-Content -LiteralPath $fastctxConfig -Encoding UTF8
    $inSection = $false
    foreach ($line in $lines) {
        if ($line -match '^\[fastshell\]\s*$') {
            $inSection = $true
            continue
        }
        if ($line -match '^\[') { $inSection = $false }
        if ($inSection -and $line -match '^\s*enabled\s*=\s*true(?:\s*(?:#.*)?)?$') {
            return $true
        }
    }
    return $false
}

function Enable-FastShell {
    New-Item -ItemType Directory -Force -Path $fastctxDirectory | Out-Null
    $lines = if (Test-Path -LiteralPath $fastctxConfig) {
        @(Get-Content -LiteralPath $fastctxConfig -Encoding UTF8)
    } else {
        @('schema_version = 1', '')
    }
    $output = [System.Collections.Generic.List[string]]::new()
    $inSection = $false
    $seenSection = $false
    $wroteEnabled = $false
    foreach ($line in $lines) {
        if ($line -match '^\[fastshell\]\s*$') {
            if ($inSection -and -not $wroteEnabled) { $output.Add('enabled = true') }
            $inSection = $true
            $seenSection = $true
            $wroteEnabled = $false
            $output.Add($line)
            continue
        }
        if ($line -match '^\[') {
            if ($inSection -and -not $wroteEnabled) { $output.Add('enabled = true') }
            $inSection = $false
        }
        if ($inSection -and $line -match '^\s*enabled\s*=') {
            $output.Add('enabled = true')
            $wroteEnabled = $true
            continue
        }
        $output.Add($line)
    }
    if ($inSection -and -not $wroteEnabled) { $output.Add('enabled = true') }
    if (-not $seenSection) {
        $output.Add('')
        $output.Add('[fastshell]')
        $output.Add('enabled = true')
    }
    $output | Set-Content -LiteralPath $fastctxConfig -Encoding UTF8
}

function Get-DownloadedBinary {
    $target = 'x86_64-pc-windows-msvc'
    $asset = $metadata.assets.$target
    if (-not $asset) { throw "FastCtx release metadata is incomplete for $target" }
    $temporaryDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $temporaryDirectory | Out-Null
    try {
        $archive = Join-Path $temporaryDirectory $asset.name
        Invoke-WebRequest -Uri $asset.url -OutFile $archive
        $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $asset.sha256) {
            throw "FastCtx archive checksum verification failed for $($asset.name)"
        }
        $extractDirectory = Join-Path $temporaryDirectory 'extract'
        Expand-Archive -LiteralPath $archive -DestinationPath $extractDirectory
        $downloaded = Get-ChildItem -LiteralPath $extractDirectory -Filter 'fastctx.exe' -File -Recurse |
            Select-Object -First 1
        if (-not $downloaded) { throw 'FastCtx archive does not contain fastctx.exe' }
        $retained = Join-Path ([System.IO.Path]::GetTempPath()) "fastctx-provision-$([System.Guid]::NewGuid().ToString('N')).exe"
        Copy-Item -LiteralPath $downloaded.FullName -Destination $retained
        return $retained
    } finally {
        if (Test-Path -LiteralPath $temporaryDirectory) {
            Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force
        }
    }
}

function Invoke-Setup {
    if ((Test-Path -LiteralPath $stableBinary) -and (Test-FastShellEnabled)) {
        $versionOutput = & $stableBinary --version 2>$null
        $installedVersion = (($versionOutput | Select-Object -First 1) -split '\s+')[1]
        if ($installedVersion -eq $metadata.version) {
            & $stableBinary status --codex-home $codexHome *> $null
            if ($LASTEXITCODE -eq 0) {
                Write-Output "FastCtx $($metadata.version) is already provisioned with shell tools enabled"
                return
            }
        }
    }
    $downloaded = Get-DownloadedBinary
    $backup = $null
    try {
        if (Test-Path -LiteralPath $fastctxConfig) {
            $backup = Join-Path ([System.IO.Path]::GetTempPath()) "fastctx-config-$([System.Guid]::NewGuid().ToString('N')).toml"
            Copy-Item -LiteralPath $fastctxConfig -Destination $backup
        }
        Enable-FastShell
        $env:FASTCTX_DISABLE_UPDATE_CHECK = '1'
        & $downloaded apply --codex-home $codexHome --tier standard --yes
        if ($LASTEXITCODE -ne 0) { throw "FastCtx Apply failed with exit code $LASTEXITCODE" }
        & $stableBinary status --codex-home $codexHome
        if ($LASTEXITCODE -ne 0) { throw "FastCtx status failed with exit code $LASTEXITCODE" }
    } catch {
        if ($backup) {
            Copy-Item -LiteralPath $backup -Destination $fastctxConfig -Force
        } elseif (Test-Path -LiteralPath $fastctxConfig) {
            Remove-Item -LiteralPath $fastctxConfig -Force
        }
        throw
    } finally {
        if ($backup -and (Test-Path -LiteralPath $backup)) { Remove-Item -LiteralPath $backup -Force }
        if (Test-Path -LiteralPath $downloaded) { Remove-Item -LiteralPath $downloaded -Force }
    }
}

switch ($action) {
    'setup' { Invoke-Setup }
    'status' {
        if (-not (Test-Path -LiteralPath $stableBinary)) { throw "FastCtx is not installed at $stableBinary" }
        $env:FASTCTX_DISABLE_UPDATE_CHECK = '1'
        & $stableBinary status --codex-home $codexHome
        exit $LASTEXITCODE
    }
    'unapply' {
        if (-not (Test-Path -LiteralPath $stableBinary)) { throw "FastCtx is not installed at $stableBinary" }
        $env:FASTCTX_DISABLE_UPDATE_CHECK = '1'
        & $stableBinary unapply --codex-home $codexHome --yes
        exit $LASTEXITCODE
    }
    default { throw 'Usage: provision.ps1 {setup|status|unapply} [--yes]' }
}
