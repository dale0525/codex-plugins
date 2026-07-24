$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$version = if ($env:CODEX_SYNC_VERSION) { $env:CODEX_SYNC_VERSION } else { '0.1.1' }
$codexHomePath = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
$installDirectory = if ($env:CODEX_SYNC_BIN_HOME) {
    $env:CODEX_SYNC_BIN_HOME
} else {
    Join-Path $codexHomePath "codex-sync\bin\$version"
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
        if (-not $checksumLine) {
            throw "Release checksum is missing for $artifact"
        }
        $expected = ($checksumLine -split '\s+')[0].ToLowerInvariant()
        $actual = (Get-FileHash -LiteralPath $artifactPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected) {
            throw "Checksum verification failed for $artifact"
        }
        New-Item -ItemType Directory -Force -Path $installDirectory | Out-Null
        Move-Item -LiteralPath $artifactPath -Destination $binaryPath -Force
    } finally {
        if (Test-Path -LiteralPath $temporaryDirectory) {
            Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force
        }
    }
}

& $binaryPath @args
exit $LASTEXITCODE
