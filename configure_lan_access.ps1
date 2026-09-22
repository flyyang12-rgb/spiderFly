param(
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
$ruleName = "SpiderFly Shared Panel (TCP $Port)"
$resultDirectory = Join-Path $PSScriptRoot 'data\setup'
$resultPath = Join-Path $resultDirectory 'firewall-result.txt'

function Write-Result([string]$Text) {
    New-Item -ItemType Directory -Path $resultDirectory -Force | Out-Null
    [System.IO.File]::WriteAllText($resultPath, $Text, [System.Text.UTF8Encoding]::new($false))
}

try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    $isAdministrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

    if (-not $isAdministrator) {
        $arguments = @(
            '-NoProfile',
            '-ExecutionPolicy', 'Bypass',
            '-File', ('"{0}"' -f $PSCommandPath),
            '-Port', $Port
        )
        $elevated = Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments -Wait -PassThru
        exit $elevated.ExitCode
    }

    Get-NetFirewallRule -DisplayName 'SpiderFly Shared Panel (TCP *)' -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule -ErrorAction SilentlyContinue

    New-NetFirewallRule `
        -DisplayName $ruleName `
        -Description 'Allow SpiderFly from the local subnet.' `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort $Port `
        -RemoteAddress LocalSubnet `
        -Profile Any `
        -EdgeTraversalPolicy Block | Out-Null

    Write-Result "OK`r`nPort=$Port`r`nRule=$ruleName"
    exit 0
} catch {
    $message = $_.Exception.Message
    try { Write-Result "ERROR`r`n$message" } catch { }
    Write-Error $message
    exit 1
}
