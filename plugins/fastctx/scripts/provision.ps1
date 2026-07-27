$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$pluginRoot = Split-Path -Parent $PSScriptRoot
$metadataPath = Join-Path $pluginRoot 'upstream-release.json'
$bashMetadataPath = Join-Path $pluginRoot 'windows-bash-runtime.json'
$action = if ($args.Count -gt 0) { $args[0] } else { 'status' }
$metadata = Get-Content -LiteralPath $metadataPath -Raw -Encoding UTF8 | ConvertFrom-Json
$bashMetadata = Get-Content -LiteralPath $bashMetadataPath -Raw -Encoding UTF8 | ConvertFrom-Json
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
$fastctxDirectory = Join-Path $env:USERPROFILE '.fastctx'
$fastctxConfig = Join-Path $fastctxDirectory 'config.toml'
$stableBinary = Join-Path $fastctxDirectory 'bin\fastctx.exe'
$managedBashRoot = Join-Path $fastctxDirectory 'portable-git'
$managedBash = Join-Path $managedBashRoot 'usr\bin\bash.exe'

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

function Test-GnuBash {
    param([Parameter(Mandatory)][string]$Path)

    if (-not [System.IO.Path]::IsPathRooted($Path) -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }
    $normalized = [System.IO.Path]::GetFullPath($Path).Replace('\', '/')
    if ($normalized -match '(?i)/Windows/System32/bash\.exe$' -or $normalized -match '(?i)/WindowsApps/bash\.exe$') {
        return $false
    }
    try {
        $output = (& $Path --version 2>&1 | Out-String)
        return $LASTEXITCODE -eq 0 -and $output.Contains('GNU bash')
    } catch {
        return $false
    }
}

function Get-AutomaticBashCandidates {
    $candidates = [System.Collections.Generic.List[string]]::new()
    foreach ($git in @(Get-Command git.exe -All -ErrorAction SilentlyContinue)) {
        $directory = Split-Path -Parent $git.Source
        for ($index = 0; $index -lt 4 -and $directory; $index++) {
            $candidates.Add((Join-Path $directory 'usr\bin\bash.exe'))
            $directory = Split-Path -Parent $directory
        }
    }
    foreach ($root in @(
        [Environment]::GetEnvironmentVariable('ProgramFiles'),
        [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
    )) {
        if ($root) { $candidates.Add((Join-Path $root 'Git\usr\bin\bash.exe')) }
    }
    $localAppData = [Environment]::GetEnvironmentVariable('LocalAppData')
    if ($localAppData) {
        $candidates.Add((Join-Path $localAppData 'Programs\Git\usr\bin\bash.exe'))
    }
    foreach ($bash in @(Get-Command bash.exe -All -ErrorAction SilentlyContinue)) {
        $candidates.Add($bash.Source)
    }
    return $candidates
}

function Resolve-UsableBash {
    $processOverride = $env:FASTCTX_BASH
    if ($processOverride) {
        if (Test-GnuBash -Path $processOverride) { return [System.IO.Path]::GetFullPath($processOverride) }
        if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
            [System.IO.Path]::GetFullPath($processOverride),
            [System.IO.Path]::GetFullPath($managedBash)
        )) {
            throw "FASTCTX_BASH is set but is not a usable standalone GNU bash: $processOverride"
        }
        Remove-Item Env:FASTCTX_BASH
    }

    $userOverride = [Environment]::GetEnvironmentVariable('FASTCTX_BASH', 'User')
    if ($userOverride) {
        if (-not (Test-GnuBash -Path $userOverride)) {
            if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
                [System.IO.Path]::GetFullPath($userOverride),
                [System.IO.Path]::GetFullPath($managedBash)
            )) {
                throw "The user FASTCTX_BASH value is not a usable standalone GNU bash: $userOverride"
            }
            [Environment]::SetEnvironmentVariable('FASTCTX_BASH', $null, 'User')
        } else {
            $env:FASTCTX_BASH = [System.IO.Path]::GetFullPath($userOverride)
            return $env:FASTCTX_BASH
        }
    }

    if (Test-GnuBash -Path $managedBash) {
        $resolved = [System.IO.Path]::GetFullPath($managedBash)
        [Environment]::SetEnvironmentVariable('FASTCTX_BASH', $resolved, 'User')
        $env:FASTCTX_BASH = $resolved
        return $resolved
    }

    $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($candidate in Get-AutomaticBashCandidates) {
        if (-not $candidate) { continue }
        $resolved = [System.IO.Path]::GetFullPath($candidate)
        if ($seen.Add($resolved) -and (Test-GnuBash -Path $resolved)) { return $resolved }
    }
    return $null
}

function Install-ManagedBash {
    if ($bashMetadata.schema_version -ne 1 -or -not $bashMetadata.asset) {
        throw 'Windows Bash runtime metadata is malformed'
    }
    $asset = $bashMetadata.asset
    $temporaryDirectory = Join-Path ([System.IO.Path]::GetTempPath()) "fastctx-bash-$([System.Guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Path $temporaryDirectory | Out-Null
    try {
        if ($asset.archive_format -ne 'tar.bz2') {
            throw "Unsupported Git for Windows runtime archive format: $($asset.archive_format)"
        }
        $archive = Join-Path $temporaryDirectory $asset.name
        Invoke-WebRequest -Uri $asset.url -OutFile $archive
        if ((Get-Item -LiteralPath $archive).Length -ne [long]$asset.size) {
            throw "Git for Windows runtime archive size verification failed for $($asset.name)"
        }
        $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $asset.sha256) {
            throw "Git for Windows runtime archive checksum verification failed for $($asset.name)"
        }
        $tar = Get-Command tar.exe -ErrorAction Stop
        $entries = @(& $tar.Source -tjf $archive)
        if ($LASTEXITCODE -ne 0) { throw "Cannot list Git for Windows runtime archive: exit code $LASTEXITCODE" }
        foreach ($entry in $entries) {
            $normalized = $entry.Replace('\', '/')
            if ($normalized -match '^/' -or $normalized -match '^[A-Za-z]:' -or $normalized -match '(^|/)\.\.(/|$)') {
                throw "Git for Windows runtime archive contains an unsafe path: $entry"
            }
        }
        $extractDirectory = Join-Path $temporaryDirectory 'extract'
        New-Item -ItemType Directory -Path $extractDirectory | Out-Null
        & $tar.Source -xjf $archive -C $extractDirectory
        if ($LASTEXITCODE -ne 0) { throw "Cannot extract Git for Windows runtime archive: exit code $LASTEXITCODE" }
        $extractedBash = Join-Path $extractDirectory 'usr\bin\bash.exe'
        if (-not (Test-GnuBash -Path $extractedBash)) {
            throw 'Git for Windows runtime archive does not contain a usable usr\bin\bash.exe'
        }

        New-Item -ItemType Directory -Force -Path $fastctxDirectory | Out-Null
        $backup = $null
        try {
            if (Test-Path -LiteralPath $managedBashRoot) {
                $backup = "$managedBashRoot.backup-$([System.Guid]::NewGuid().ToString('N'))"
                Move-Item -LiteralPath $managedBashRoot -Destination $backup
            }
            Move-Item -LiteralPath $extractDirectory -Destination $managedBashRoot
            if ($backup) { Remove-Item -LiteralPath $backup -Recurse -Force }
        } catch {
            if (Test-Path -LiteralPath $managedBashRoot) {
                Remove-Item -LiteralPath $managedBashRoot -Recurse -Force
            }
            if ($backup -and (Test-Path -LiteralPath $backup)) {
                Move-Item -LiteralPath $backup -Destination $managedBashRoot
            }
            throw
        }
    } finally {
        if (Test-Path -LiteralPath $temporaryDirectory) {
            Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force
        }
    }

    if (-not (Test-GnuBash -Path $managedBash)) { throw 'Managed Portable Git installation failed validation' }
    $resolved = [System.IO.Path]::GetFullPath($managedBash)
    [Environment]::SetEnvironmentVariable('FASTCTX_BASH', $resolved, 'User')
    $env:FASTCTX_BASH = $resolved
    return $resolved
}

function Initialize-BashEnvironment {
    param([switch]$AllowInstall)

    $bash = Resolve-UsableBash
    if (-not $bash) {
        if (-not $AllowInstall) {
            throw 'Cannot find a usable standalone GNU bash; run FastCtx setup to install the reviewed portable runtime'
        }
        $bash = Install-ManagedBash
    }
    $env:FASTCTX_BASH = $bash
    return $bash
}

function Remove-ManagedBashEnvironment {
    $userValue = [Environment]::GetEnvironmentVariable('FASTCTX_BASH', 'User')
    if ($userValue -and [System.StringComparer]::OrdinalIgnoreCase.Equals(
        [System.IO.Path]::GetFullPath($userValue),
        [System.IO.Path]::GetFullPath($managedBash)
    )) {
        [Environment]::SetEnvironmentVariable('FASTCTX_BASH', $null, 'User')
    }
    if ($env:FASTCTX_BASH -and [System.StringComparer]::OrdinalIgnoreCase.Equals(
        [System.IO.Path]::GetFullPath($env:FASTCTX_BASH),
        [System.IO.Path]::GetFullPath($managedBash)
    )) {
        Remove-Item Env:FASTCTX_BASH
    }
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
    $null = Initialize-BashEnvironment -AllowInstall
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
        $null = Initialize-BashEnvironment
        $env:FASTCTX_DISABLE_UPDATE_CHECK = '1'
        & $stableBinary status --codex-home $codexHome
        exit $LASTEXITCODE
    }
    'unapply' {
        if (-not (Test-Path -LiteralPath $stableBinary)) { throw "FastCtx is not installed at $stableBinary" }
        $env:FASTCTX_DISABLE_UPDATE_CHECK = '1'
        & $stableBinary unapply --codex-home $codexHome --yes
        $exitCode = $LASTEXITCODE
        if ($exitCode -eq 0) { Remove-ManagedBashEnvironment }
        exit $exitCode
    }
    default { throw 'Usage: provision.ps1 {setup|status|unapply} [--yes]' }
}
