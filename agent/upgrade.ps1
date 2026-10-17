param([string]$InstallDir,[string]$DataDir=(Join-Path $env:LOCALAPPDATA 'SpiderFlyAgent'))
$ErrorActionPreference='Stop'
# Python launched from PowerShell 7 can inherit incompatible module paths.
# This process and its Windows PowerShell children use only Windows modules.
$env:PSModulePath=@((Join-Path $PSHOME 'Modules'),(Join-Path $env:ProgramFiles 'WindowsPowerShell/Modules')) -join [IO.Path]::PathSeparator
if(-not $InstallDir -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.venv/Scripts/python.exe'))){$InstallDir=$PSScriptRoot}
. (Join-Path $PSScriptRoot 'upgrade-core.ps1')
. (Join-Path $PSScriptRoot 'upgrade-ui.ps1')
$form=New-AgentUpgradeForm $PSScriptRoot $InstallDir $DataDir
try{[void]$form.ShowDialog()}finally{$form.Dispose()}
