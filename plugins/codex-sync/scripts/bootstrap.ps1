$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$version = if ($env:CODEX_SYNC_VERSION) { $env:CODEX_SYNC_VERSION } else { '0.6.7' }
$codexHomePath = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
$installDirectory = if ($env:CODEX_SYNC_BIN_HOME) {
    $env:CODEX_SYNC_BIN_HOME
} else {
    Join-Path $codexHomePath "codex-sync\bin\$version"
}
$pluginRoot = Split-Path -Parent $PSScriptRoot
$gitMetadataPath = Join-Path $pluginRoot 'windows-git-runtime.json'

function Test-GitExecutable {
    param([Parameter(Mandatory)][string]$Path)

    if (-not [System.IO.Path]::IsPathRooted($Path) -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }
    try {
        $output = (& $Path --version 2>&1 | Out-String)
        return $LASTEXITCODE -eq 0 -and $output -match '(?m)^git version '
    } catch {
        return $false
    }
}

function Test-PathInside {
    param(
        [Parameter(Mandatory)][string]$Root,
        [Parameter(Mandatory)][string]$Candidate
    )

    try {
        $normalizedRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
        $normalizedCandidate = [System.IO.Path]::GetFullPath($Candidate)
        $prefix = "$normalizedRoot$([System.IO.Path]::DirectorySeparatorChar)"
        return $normalizedCandidate.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
    } catch {
        return $false
    }
}

function Assert-PathInside {
    param(
        [Parameter(Mandatory)][string]$Root,
        [Parameter(Mandatory)][string]$Candidate,
        [Parameter(Mandatory)][string]$Description
    )

    if (-not (Test-PathInside -Root $Root -Candidate $Candidate)) {
        throw "$Description escapes its expected directory"
    }
}

function Get-MetadataValue {
    param(
        [Parameter(Mandatory)]$Object,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Description
    )

    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { throw "Codex Sync Windows Git runtime metadata is missing $Description" }
    return $property.Value
}

function Assert-GitRuntimeMetadata {
    param([Parameter(Mandatory)]$Metadata)

    $schema = Get-MetadataValue -Object $Metadata -Name 'schema_version' -Description 'schema_version'
    $repository = Get-MetadataValue -Object $Metadata -Name 'repository' -Description 'repository'
    $version = Get-MetadataValue -Object $Metadata -Name 'version' -Description 'version'
    $tag = Get-MetadataValue -Object $Metadata -Name 'tag' -Description 'tag'
    $asset = Get-MetadataValue -Object $Metadata -Name 'asset' -Description 'asset'
    if ($schema -ne 1 -or $repository -ne 'git-for-windows/git' -or $asset -isnot [psobject]) {
        throw 'Codex Sync Windows Git runtime metadata is malformed'
    }
    if ($version -isnot [string] -or $version -notmatch '^\d+(?:\.\d+){2,3}$') {
        throw 'Codex Sync Windows Git runtime metadata has an invalid version'
    }
    if ($tag -isnot [string] -or $tag -notmatch '^v\d+(?:\.\d+){2,3}\.windows\.\d+$') {
        throw 'Codex Sync Windows Git runtime metadata has an invalid tag'
    }
    $name = Get-MetadataValue -Object $asset -Name 'name' -Description 'asset.name'
    $url = Get-MetadataValue -Object $asset -Name 'url' -Description 'asset.url'
    $size = Get-MetadataValue -Object $asset -Name 'size' -Description 'asset.size'
    $sha256 = Get-MetadataValue -Object $asset -Name 'sha256' -Description 'asset.sha256'
    $archiveFormat = Get-MetadataValue -Object $asset -Name 'archive_format' -Description 'asset.archive_format'
    if ($name -isnot [string] -or $name -ne "Git-$version-64-bit.tar.bz2") {
        throw 'Codex Sync Windows Git runtime metadata has an invalid asset name'
    }
    if ($url -isnot [string] -or $url -ne "https://github.com/git-for-windows/git/releases/download/$tag/$name") {
        throw 'Codex Sync Windows Git runtime metadata has an invalid asset URL'
    }
    if ($size -isnot [long] -or $size -le 0 -or $sha256 -isnot [string] -or $sha256 -notmatch '^[0-9a-f]{64}$' -or $archiveFormat -ne 'tar.bz2') {
        throw 'Codex Sync Windows Git runtime metadata is malformed'
    }
}

function Get-GitCandidates {
    param([Parameter(Mandatory)][string]$ManagedGit)

    $candidates = [System.Collections.Generic.List[string]]::new()
    $candidates.Add($ManagedGit)
    if ($env:USERPROFILE) {
        $candidates.Add((Join-Path $env:USERPROFILE '.fastctx\portable-git\cmd\git.exe'))
    }
    foreach ($git in @(Get-Command git.exe -All -ErrorAction SilentlyContinue)) {
        $candidates.Add($git.Source)
    }
    foreach ($root in @(
        [Environment]::GetEnvironmentVariable('ProgramFiles'),
        [Environment]::GetEnvironmentVariable('ProgramFiles(x86)'),
        [Environment]::GetEnvironmentVariable('LocalAppData')
    )) {
        if (-not $root) { continue }
        if ($root -eq [Environment]::GetEnvironmentVariable('LocalAppData')) {
            $candidates.Add((Join-Path $root 'Programs\Git\cmd\git.exe'))
        } else {
            $candidates.Add((Join-Path $root 'Git\cmd\git.exe'))
        }
    }
    return $candidates
}

function Resolve-UsableGit {
    param([Parameter(Mandatory)][string]$ManagedGit)

    if ($env:CODEX_SYNC_GIT_BIN) {
        if (-not (Test-GitExecutable -Path $env:CODEX_SYNC_GIT_BIN)) {
            throw "CODEX_SYNC_GIT_BIN is set but is not a usable Git executable: $env:CODEX_SYNC_GIT_BIN"
        }
        return [System.IO.Path]::GetFullPath($env:CODEX_SYNC_GIT_BIN)
    }
    $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($candidate in Get-GitCandidates -ManagedGit $ManagedGit) {
        if (-not $candidate) { continue }
        $resolved = [System.IO.Path]::GetFullPath($candidate)
        if ($seen.Add($resolved) -and (Test-GitExecutable -Path $resolved)) { return $resolved }
    }
    return $null
}

function Install-ManagedGit {
    param(
        [Parameter(Mandatory)]$Metadata,
        [Parameter(Mandatory)][string]$ManagedRoot,
        [Parameter(Mandatory)][string]$ManagedGit
    )

    Assert-GitRuntimeMetadata -Metadata $Metadata
    Assert-PathInside -Root (Split-Path -Parent $ManagedRoot) -Candidate $ManagedRoot -Description 'Managed Git runtime path'
    $asset = Get-MetadataValue -Object $Metadata -Name 'asset' -Description 'asset'
    $temporaryDirectory = Join-Path ([System.IO.Path]::GetTempPath()) "codex-sync-git-$([System.Guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Path $temporaryDirectory | Out-Null
    try {
        $archive = [System.IO.Path]::GetFullPath((Join-Path $temporaryDirectory $asset.name))
        Assert-PathInside -Root $temporaryDirectory -Candidate $archive -Description 'Git runtime archive path'
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
        $extractDirectory = [System.IO.Path]::GetFullPath((Join-Path $temporaryDirectory 'extract'))
        Assert-PathInside -Root $temporaryDirectory -Candidate $extractDirectory -Description 'Git runtime extraction path'
        New-Item -ItemType Directory -Path $extractDirectory | Out-Null
        & $tar.Source -xjf $archive -C $extractDirectory
        if ($LASTEXITCODE -ne 0) { throw "Cannot extract Git for Windows runtime archive: exit code $LASTEXITCODE" }
        foreach ($entry in Get-ChildItem -LiteralPath $extractDirectory -Recurse -Force) {
            if (($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq 0) { continue }
            $resolved = (Resolve-Path -LiteralPath $entry.FullName -ErrorAction Stop).Path
            Assert-PathInside -Root $extractDirectory -Candidate $resolved -Description "Git runtime link $($entry.FullName)"
        }
        $extractedGit = Join-Path $extractDirectory 'cmd\git.exe'
        $resolvedGit = (Resolve-Path -LiteralPath $extractedGit -ErrorAction Stop).Path
        Assert-PathInside -Root $extractDirectory -Candidate $resolvedGit -Description 'Git runtime executable'
        if (-not (Test-GitExecutable -Path $extractedGit)) {
            throw 'Git for Windows runtime archive does not contain a usable cmd\git.exe'
        }

        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ManagedRoot) | Out-Null
        $backup = $null
        try {
            if (Test-Path -LiteralPath $ManagedRoot) {
                $backup = "$ManagedRoot.backup-$([System.Guid]::NewGuid().ToString('N'))"
                Move-Item -LiteralPath $ManagedRoot -Destination $backup
            }
            Move-Item -LiteralPath $extractDirectory -Destination $ManagedRoot
            if ($backup) { Remove-Item -LiteralPath $backup -Recurse -Force }
        } catch {
            if (Test-Path -LiteralPath $ManagedRoot) { Remove-Item -LiteralPath $ManagedRoot -Recurse -Force }
            if ($backup -and (Test-Path -LiteralPath $backup)) { Move-Item -LiteralPath $backup -Destination $ManagedRoot }
            throw
        }
    } finally {
        if (Test-Path -LiteralPath $temporaryDirectory) { Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force }
    }
    if (-not (Test-GitExecutable -Path $ManagedGit)) { throw 'Managed Portable Git installation failed validation' }
    return [System.IO.Path]::GetFullPath($ManagedGit)
}

function Initialize-GitEnvironment {
    if (-not (Test-Path -LiteralPath $gitMetadataPath -PathType Leaf)) {
        throw "Codex Sync Windows Git runtime metadata is missing: $gitMetadataPath"
    }
    $metadata = Get-Content -LiteralPath $gitMetadataPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-GitRuntimeMetadata -Metadata $metadata
    $portableGitRoot = [System.IO.Path]::GetFullPath((Join-Path $codexHomePath 'codex-sync\portable-git'))
    $runtimeVersion = Get-MetadataValue -Object $metadata -Name 'version' -Description 'version'
    $managedRoot = [System.IO.Path]::GetFullPath((Join-Path $portableGitRoot $runtimeVersion))
    Assert-PathInside -Root $portableGitRoot -Candidate $managedRoot -Description 'Managed Git runtime path'
    $managedGit = Join-Path $managedRoot 'cmd\git.exe'
    $git = Resolve-UsableGit -ManagedGit $managedGit
    if (-not $git) {
        if ($env:CODEX_SYNC_OFFLINE -eq '1') {
            throw "Git is unavailable and offline mode forbids downloading Codex Sync's verified portable Git runtime"
        }
        $git = Install-ManagedGit -Metadata $metadata -ManagedRoot $managedRoot -ManagedGit $managedGit
    }
    $env:CODEX_SYNC_GIT_BIN = $git
    $gitDirectory = Split-Path -Parent $git
    $pathEntries = @($env:PATH -split [regex]::Escape([System.IO.Path]::PathSeparator))
    if (-not ($pathEntries | Where-Object { [System.StringComparer]::OrdinalIgnoreCase.Equals($_, $gitDirectory) })) {
        $env:PATH = "$gitDirectory$([System.IO.Path]::PathSeparator)$env:PATH"
    }
}

if ($args.Count -gt 0 -and $args[0] -in @('setup', 'pull', 'push')) {
    Initialize-GitEnvironment
}

if ($env:CODEX_SYNC_BIN) {
    & $env:CODEX_SYNC_BIN @args
    exit $LASTEXITCODE
}

$architecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
if ($architecture -ne 'X64') {
    throw "Unsupported Windows architecture: $architecture"
}
$artifact = 'codex-sync-x86_64-pc-windows-msvc.exe'
$binaryPath = Join-Path $installDirectory 'codex-sync.exe'

if (-not (Test-Path -LiteralPath $binaryPath)) {
    if ($env:CODEX_SYNC_OFFLINE -eq '1') {
        throw 'Codex Sync engine is not cached and offline mode forbids downloading it'
    }
    $temporaryDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $temporaryDirectory | Out-Null
    try {
        $releaseBase = "https://github.com/dale0525/codex-plugins/releases/download/codex-sync-v$version"
        $artifactPath = Join-Path $temporaryDirectory $artifact
        $checksumsPath = Join-Path $temporaryDirectory 'checksums.txt'
        Invoke-WebRequest -Uri "$releaseBase/$artifact" -OutFile $artifactPath
        Invoke-WebRequest -Uri "$releaseBase/checksums.txt" -OutFile $checksumsPath
        $checksumLine = Get-Content -LiteralPath $checksumsPath -Encoding UTF8 |
            Where-Object { $_ -match "^[0-9a-fA-F]{64}\s+$([regex]::Escape($artifact))$" } |
            Select-Object -First 1
        if (-not $checksumLine) { throw "Release checksum is missing for $artifact" }
        $expected = ($checksumLine -split '\s+')[0].ToLowerInvariant()
        $actual = (Get-FileHash -LiteralPath $artifactPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected) { throw "Checksum verification failed for $artifact" }
        New-Item -ItemType Directory -Force -Path $installDirectory | Out-Null
        Move-Item -LiteralPath $artifactPath -Destination $binaryPath -Force
    } finally {
        if (Test-Path -LiteralPath $temporaryDirectory) { Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force }
    }
}

& $binaryPath @args
exit $LASTEXITCODE
