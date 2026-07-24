$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$version = if ($env:CODEX_SYNC_VERSION) { $env:CODEX_SYNC_VERSION } else { '0.1.0' }
$codexHomePath = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
$binaryDirectory = if ($env:CODEX_SYNC_BIN_HOME) {
    $env:CODEX_SYNC_BIN_HOME
} else {
    Join-Path $codexHomePath "codex-sync\bin\$version"
}
$binaryPath = if ($env:CODEX_SYNC_BIN) { $env:CODEX_SYNC_BIN } else { Join-Path $binaryDirectory 'codex-sync.exe' }

if (Test-Path -LiteralPath $binaryPath) {
    & $binaryPath check-update
}
