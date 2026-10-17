param(
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [Parameter(Mandatory=$true)][string]$DataDir,
    [Parameter(Mandatory=$true)][string]$JobDir,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{32}$')][string]$Token
)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$env:PSModulePath=@((Join-Path $PSHOME 'Modules'),(Join-Path $env:ProgramFiles 'WindowsPowerShell/Modules')) -join [IO.Path]::PathSeparator
. (Join-Path $PSScriptRoot 'upgrade-core.ps1')
try{
    Invoke-AgentUpgrade -InstallDir $InstallDir -DataDir $DataDir -PackagePath (Join-Path $JobDir 'package.zip') -JobDir $JobDir -PauseToken $Token -DeferResume
}catch{
    Set-UpgradeProgress $JobDir $_.Exception.Message $true $false
    exit 1
}
