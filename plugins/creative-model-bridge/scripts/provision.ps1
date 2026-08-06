param(
  [Parameter(Position = 0)] [ValidateSet('run','cli','exec','cache','install','migrate')] [string]$Action = 'run',
  [Parameter(ValueFromRemainingArguments = $true)] [string[]]$RemainingArgs
)

$ErrorActionPreference = 'Stop'
$null = [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$version = if ($env:CREATIVE_MODEL_BRIDGE_VERSION) { $env:CREATIVE_MODEL_BRIDGE_VERSION } else { '0.2.0' }
if ($version -notmatch '^\d+\.\d+\.\d+$') { throw 'creative-model-bridge: invalid version' }

$override = $env:CREATIVE_MODEL_BRIDGE_BIN
$codexHome = if ($env:CODEX_HOME) { [Environment]::ExpandEnvironmentVariables($env:CODEX_HOME) } else { Join-Path $HOME '.codex' }
$codexHome = [IO.Path]::GetFullPath($codexHome)
$target = 'x86_64-pc-windows-msvc'
$asset = 'creative-model-bridge-x86_64-pc-windows-msvc.exe'
$runtime = Join-Path $codexHome ('creative-model-bridge\runtime\v' + $version)
$targetRoot = Join-Path $runtime ('objects\' + $target)
$active = Join-Path $targetRoot 'active'
New-Item -ItemType Directory -Force -Path $targetRoot | Out-Null

function Get-Sha256([string]$Path) {
  $hash = $null
  if (Get-Command -Name Get-FileHash -ErrorAction SilentlyContinue) {
    try {
      $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash
    } catch {
      $hash = $null
    }
  }
  if (-not $hash) {
    $stream = [IO.File]::OpenRead($Path)
    try {
      $algorithm = [Security.Cryptography.SHA256]::Create()
      try {
        $bytes = $algorithm.ComputeHash($stream)
      } finally {
        $algorithm.Dispose()
      }
    } finally {
      $stream.Dispose()
    }
    $hash = -join ($bytes | ForEach-Object { $_.ToString('x2') })
  }
  $hash = $hash.ToLowerInvariant()
  if ($hash -notmatch '^[0-9a-f]{64}$') { throw 'creative-model-bridge: invalid SHA-256 digest' }
  return $hash
}

function Get-CachedBinary {
  if (-not (Test-Path -LiteralPath $active -PathType Leaf)) { return $null }
  $current = Get-Content -LiteralPath $active
  if ($current.Count -ne 3 -or $current[0] -ne 'cmb-active-v4' -or $current[1] -notmatch '^[0-9a-f]{64}$' -or $current[2] -notmatch '^[A-Za-z0-9._-]+$' -or $current[2] -in @('.', '..')) { return $null }
  $candidate = Join-Path $targetRoot ($current[1] + '\' + $current[2] + '\' + $asset)
  $complete = Join-Path (Split-Path $candidate -Parent) 'complete'
  $marker = if (Test-Path -LiteralPath $complete -PathType Leaf) { Get-Content -LiteralPath $complete } else { @() }
  if (-not (Test-Path -LiteralPath $candidate -PathType Leaf) -or $marker.Count -ne 3 -or $marker[0] -ne 'cmb-object-v4' -or $marker[1] -ne $current[1] -or $marker[2] -ne $current[2]) { return $null }
  if ((Get-Sha256 $candidate) -ne $current[1]) { return $null }
  return $candidate
}

function Publish-LocalOverride([string]$Candidate) {
  $digest = Get-Sha256 $Candidate
  $generation = 'local.' + $PID.ToString() + '.' + [Guid]::NewGuid().ToString('N')
  $object = Join-Path $targetRoot ($digest + '\' + $generation)
  $stage = Join-Path $targetRoot ('staging.' + $generation)
  New-Item -ItemType Directory -Force -Path $stage | Out-Null
  try {
    Copy-Item -LiteralPath $Candidate -Destination (Join-Path $stage $asset)
    $complete = Join-Path $object 'complete'
    New-Item -ItemType Directory -Force -Path $object | Out-Null
    Move-Item -LiteralPath (Join-Path $stage $asset) -Destination (Join-Path $object $asset)
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $completeTemp = Join-Path $object ('.complete.' + $generation)
    [IO.File]::WriteAllText($completeTemp, "cmb-object-v4`n$digest`n$generation`n", $utf8)
    Move-Item -LiteralPath $completeTemp -Destination $complete -Force
    $pointerTemp = Join-Path $targetRoot ('.active.' + $generation)
    [IO.File]::WriteAllText($pointerTemp, "cmb-active-v4`n$digest`n$generation`n", $utf8)
    Move-Item -LiteralPath $pointerTemp -Destination $active -Force
  } finally {
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
  }
  return (Get-CachedBinary)
}

$binary = $null
$overrideCandidate = $null
if ($override) {
  $candidate = [IO.Path]::GetFullPath($override)
  if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { throw 'creative-model-bridge: override is not a file' }
  if ($Action -in @('run','cli','exec','migrate')) { $binary = $candidate }
  else { $overrideCandidate = $candidate }
} elseif (Test-Path -LiteralPath $active -PathType Leaf) {
  $binary = Get-CachedBinary
}

if ($overrideCandidate) {
  $overrideDigest = Get-Sha256 $overrideCandidate
  $cached = Get-CachedBinary
  if ($cached -and (Get-Sha256 $cached) -eq $overrideDigest) { $binary = $cached }
  else { $binary = Publish-LocalOverride $overrideCandidate }
  if (-not $binary) { throw 'creative-model-bridge: local override cache publication failed' }
}

if (-not $binary) {
  if ($env:CREATIVE_MODEL_BRIDGE_OFFLINE -eq '1') { throw 'creative-model-bridge: cached runtime is unavailable (offline mode)' }
  $lock = Join-Path $targetRoot '.download.lock'
  $attempts = 0
  $token = $PID.ToString() + '.' + [Guid]::NewGuid().ToString('N')
  while ($true) {
    try { New-Item -ItemType Directory -Path $lock -ErrorAction Stop | Out-Null; break }
    catch {
      $attempts++
      $owner = Get-ChildItem -LiteralPath $lock -Filter 'owner.*' -ErrorAction SilentlyContinue | Select-Object -First 1
      $ownerPid = $null
      if ($owner) { $ownerPid = (Get-Content -LiteralPath $owner.FullName | Where-Object { $_ -like 'pid=*' } | Select-Object -First 1) -replace '^pid=', '' }
      $dead = $false
      if ($ownerPid -match '^\d+$') { $dead = -not (Get-Process -Id ([int]$ownerPid) -ErrorAction SilentlyContinue) }
      $aged = (Test-Path -LiteralPath $lock) -and (((Get-Date) - (Get-Item -LiteralPath $lock).LastWriteTime).TotalSeconds -gt 300)
      if ($dead -or ((-not $owner) -and $aged)) { Move-Item -LiteralPath $lock -Destination (Join-Path $targetRoot ('retired-lock-stale.' + $token + '.' + $attempts)) -Force -ErrorAction SilentlyContinue }
      if ($attempts -gt 600) { throw 'creative-model-bridge: timed out waiting for download' }
      Start-Sleep -Milliseconds 100
    }
  }
  $ownerMarker = Join-Path $lock ('owner.' + $token)
  Set-Content -LiteralPath $ownerMarker -Value @('pid=' + $PID, 'token=' + $token, 'started=' + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) -Encoding UTF8
  $binary = Get-CachedBinary
  $stage = Join-Path $targetRoot ('staging.' + $token)
  try {
    if (-not $binary) {
    New-Item -ItemType Directory -Path $stage | Out-Null
    $release = 'https://github.com/dale0525/codex-plugins/releases/download/creative-model-bridge-v' + $version
    Invoke-WebRequest -UseBasicParsing -Uri ($release + '/' + $asset) -OutFile (Join-Path $stage $asset)
    Invoke-WebRequest -UseBasicParsing -Uri ($release + '/checksums.txt') -OutFile (Join-Path $stage 'checksums.txt')
    $expected = $null
    foreach ($line in Get-Content -LiteralPath (Join-Path $stage 'checksums.txt')) {
      $fields = $line -split '\s+'
      if ($fields.Count -eq 2 -and $fields[0] -match '^[0-9a-f]{64}$' -and $fields[1] -eq $asset) { if ($expected) { throw 'creative-model-bridge: duplicate checksum entry' }; $expected = $fields[0] }
    }
    if (-not $expected) { throw 'creative-model-bridge: checksum entry is missing' }
    $digest = Get-Sha256 (Join-Path $stage $asset)
    if ($digest -ne $expected) { throw 'creative-model-bridge: checksum verification failed' }
    $generation = $token
    $object = Join-Path $targetRoot ($digest + '\' + $generation)
    New-Item -ItemType Directory -Force -Path $object | Out-Null
    Move-Item -LiteralPath (Join-Path $stage $asset) -Destination (Join-Path $object $asset)
    Set-Content -LiteralPath (Join-Path $object 'complete') -Value @('cmb-object-v4', $digest, $generation) -Encoding UTF8
    $pointer = Join-Path $targetRoot ('.active.' + $token)
    Set-Content -LiteralPath $pointer -Value @('cmb-active-v4', $digest, $generation) -Encoding UTF8
    Move-Item -LiteralPath $pointer -Destination $active -Force
    $binary = Join-Path $object $asset
    }
  } finally {
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $ownerMarker -PathType Leaf) {
      Remove-Item -LiteralPath $lock -Recurse -Force -ErrorAction SilentlyContinue
    }
  }
}

$env:CREATIVE_MODEL_BRIDGE_EXECUTABLE = $binary
if ($Action -eq 'cache') {
  exit 0
} elseif ($Action -eq 'install') {
  & $binary 'migrate' '--codex-home' $codexHome @RemainingArgs
} elseif ($Action -eq 'migrate') {
  & $binary 'migrate' @RemainingArgs
} else {
  & $binary 'run' @RemainingArgs
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
