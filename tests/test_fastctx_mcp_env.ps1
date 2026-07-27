$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$module = Join-Path (Split-Path -Parent $PSScriptRoot) 'plugins/fastctx/scripts/fastctx-mcp-env.ps1'
. $module

$temporary = Join-Path ([System.IO.Path]::GetTempPath()) "fastctx-mcp-env-test-$([System.Guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    $config = Join-Path $temporary 'config.toml'
    $source = @(
        'model = "test"',
        '',
        '[mcp_servers.fastctx]',
        'command = "C:/Users/test/.fastctx/bin/fastctx.exe"',
        '',
        '[mcp_servers.fastctx.env]',
        'FASTCTX_TOKEN_BUDGET = "12000"',
        'CUSTOM_ENV = "keep"',
        '',
        '[features.code_mode]',
        'direct_only_tool_namespaces = ["mcp__fastctx"]',
        ''
    ) -join "`r`n"
    [System.IO.File]::WriteAllText($config, $source, [System.Text.UTF8Encoding]::new($false))
    $bash = Join-Path $temporary 'portable git/usr/bin/bash.exe'

    Set-FastCtxMcpBashEnvironment -ConfigPath $config -BashPath $bash
    Assert-FastCtxMcpBashEnvironment -ConfigPath $config -BashPath $bash
    $expected = ConvertTo-FastCtxTomlPath -Path $bash
    if ((Get-FastCtxMcpBashEnvironment -ConfigPath $config) -ne $expected) {
        throw 'FASTCTX_BASH did not round-trip through the Codex config'
    }
    $first = [System.IO.File]::ReadAllText($config)
    if (-not $first.Contains('CUSTOM_ENV = "keep"')) { throw 'An unowned MCP env key was lost' }
    if (-not $first.Contains("`r`n")) { throw 'The existing CRLF newline style was not preserved' }
    if ([regex]::Matches($first, '(?m)^FASTCTX_BASH\s*=').Count -ne 1) {
        throw 'FASTCTX_BASH must be written exactly once'
    }

    Set-FastCtxMcpBashEnvironment -ConfigPath $config -BashPath $bash
    $second = [System.IO.File]::ReadAllText($config)
    if ($second -cne $first) { throw 'Writing the same FASTCTX_BASH value is not idempotent' }

    $replacement = Join-Path $temporary 'replacement/bash.exe'
    Set-FastCtxMcpBashEnvironment -ConfigPath $config -BashPath $replacement
    Assert-FastCtxMcpBashEnvironment -ConfigPath $config -BashPath $replacement
    $replaced = [System.IO.File]::ReadAllText($config)
    if ([regex]::Matches($replaced, '(?m)^FASTCTX_BASH\s*=').Count -ne 1) {
        throw 'Replacing FASTCTX_BASH created a duplicate key'
    }

    $mismatchRejected = $false
    try {
        Assert-FastCtxMcpBashEnvironment -ConfigPath $config -BashPath $bash
    } catch {
        $mismatchRejected = $true
    }
    if (-not $mismatchRejected) { throw 'A device-local Bash path mismatch was not rejected' }
    Write-Output 'FastCtx MCP env tests passed'
} finally {
    if (Test-Path -LiteralPath $temporary) {
        Remove-Item -LiteralPath $temporary -Recurse -Force
    }
}
