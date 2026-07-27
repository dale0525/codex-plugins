$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function ConvertTo-FastCtxTomlPath {
    param([Parameter(Mandatory)][string]$Path)

    return [System.IO.Path]::GetFullPath($Path).Replace('\', '/')
}

function Get-FastCtxMcpEnvSection {
    param(
        [Parameter(Mandatory)]
        [AllowEmptyCollection()]
        [AllowEmptyString()]
        [System.Collections.Generic.List[string]]$Lines
    )

    $headers = [System.Collections.Generic.List[int]]::new()
    for ($index = 0; $index -lt $Lines.Count; $index++) {
        if ($Lines[$index] -cmatch '^\s*\[mcp_servers\.fastctx\.env\]\s*(?:#.*)?$') {
            $headers.Add($index)
        }
    }
    if ($headers.Count -ne 1) {
        throw "Expected exactly one [mcp_servers.fastctx.env] table, found $($headers.Count)"
    }
    $end = $Lines.Count
    for ($index = $headers[0] + 1; $index -lt $Lines.Count; $index++) {
        if ($Lines[$index] -match '^\s*\[') {
            $end = $index
            break
        }
    }
    return [PSCustomObject]@{ Start = $headers[0]; End = $end }
}

function Get-FastCtxMcpBashEnvironment {
    param([Parameter(Mandatory)][string]$ConfigPath)

    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        throw "Codex config does not exist: $ConfigPath"
    }
    $source = [System.IO.File]::ReadAllText($ConfigPath)
    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.AddRange([string[]][regex]::Split($source, '\r?\n'))
    $section = Get-FastCtxMcpEnvSection -Lines $lines
    $values = [System.Collections.Generic.List[string]]::new()
    for ($index = $section.Start + 1; $index -lt $section.End; $index++) {
        if ($lines[$index] -cmatch '^\s*FASTCTX_BASH\s*=') {
            if ($lines[$index] -cnotmatch '^\s*FASTCTX_BASH\s*=\s*"([^"]*)"\s*(?:#.*)?$') {
                throw 'mcp_servers.fastctx.env.FASTCTX_BASH is not a plain TOML basic string'
            }
            $values.Add($Matches[1])
        }
    }
    if ($values.Count -gt 1) {
        throw 'mcp_servers.fastctx.env.FASTCTX_BASH is declared more than once'
    }
    if ($values.Count -eq 0) { return $null }
    return $values[0]
}

function Test-FastCtxMcpEnvironmentTable {
    param([Parameter(Mandatory)][string]$ConfigPath)

    try {
        $null = Get-FastCtxMcpBashEnvironment -ConfigPath $ConfigPath
        return $true
    } catch {
        return $false
    }
}

function Set-FastCtxMcpBashEnvironment {
    param(
        [Parameter(Mandatory)][string]$ConfigPath,
        [Parameter(Mandatory)][string]$BashPath
    )

    $source = [System.IO.File]::ReadAllText($ConfigPath)
    $newline = if ($source.Contains("`r`n")) { "`r`n" } else { "`n" }
    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.AddRange([string[]][regex]::Split($source, '\r?\n'))
    $section = Get-FastCtxMcpEnvSection -Lines $lines
    $indices = [System.Collections.Generic.List[int]]::new()
    for ($index = $section.Start + 1; $index -lt $section.End; $index++) {
        if ($lines[$index] -cmatch '^\s*FASTCTX_BASH\s*=') { $indices.Add($index) }
    }
    if ($indices.Count -gt 1) {
        throw 'mcp_servers.fastctx.env.FASTCTX_BASH is declared more than once'
    }
    $normalized = ConvertTo-FastCtxTomlPath -Path $BashPath
    $entry = "FASTCTX_BASH = `"$normalized`""
    if ($indices.Count -eq 1) {
        $lines[$indices[0]] = $entry
    } else {
        $lines.Insert($section.Start + 1, $entry)
    }

    $temporary = "$ConfigPath.fastctx-$([System.Guid]::NewGuid().ToString('N')).tmp"
    try {
        $utf8 = [System.Text.UTF8Encoding]::new($false)
        [System.IO.File]::WriteAllText($temporary, ($lines -join $newline), $utf8)
        Move-Item -LiteralPath $temporary -Destination $ConfigPath -Force
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
    }
}

function Assert-FastCtxMcpBashEnvironment {
    param(
        [Parameter(Mandatory)][string]$ConfigPath,
        [Parameter(Mandatory)][string]$BashPath
    )

    $expected = ConvertTo-FastCtxTomlPath -Path $BashPath
    $actual = Get-FastCtxMcpBashEnvironment -ConfigPath $ConfigPath
    if (-not $actual -or -not [System.StringComparer]::OrdinalIgnoreCase.Equals($actual, $expected)) {
        throw "Codex MCP FASTCTX_BASH is missing or does not match this device: expected $expected"
    }
}
