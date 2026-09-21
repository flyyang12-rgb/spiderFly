param(
    [string]$Server,
    [string]$Code,
    [string]$Name = $env:COMPUTERNAME,
    [string]$DataDir = (Join-Path $env:LOCALAPPDATA 'SpiderFlyAgent'),
    [switch]$Dispatch,
    [switch]$Console
)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $agentPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
    $createdEnvironment = $false
    if (-not (Test-Path -LiteralPath $agentPython)) {
        python -m venv (Join-Path $PSScriptRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw '创建 Agent 环境失败，需要 Python 3.12' }
        $createdEnvironment = $true
    }
    $requirementsPath = Join-Path $PSScriptRoot 'requirements.txt'
    $hashAlgorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $requirementsHash = [System.BitConverter]::ToString(
            $hashAlgorithm.ComputeHash([System.IO.File]::ReadAllBytes($requirementsPath))
        ).Replace('-', '')
    } finally { $hashAlgorithm.Dispose() }
    $dependencyMarker = Join-Path $PSScriptRoot '.venv/.dependencies.sha256'
    $installedHash = if (Test-Path -LiteralPath $dependencyMarker) {
        ([string](Get-Content -LiteralPath $dependencyMarker -Raw)).Trim()
    } else { '' }
    if ($createdEnvironment -or $installedHash -ne $requirementsHash) {
        & $agentPython -m pip install -r $requirementsPath
        if ($LASTEXITCODE -ne 0) { throw '安装 Agent 依赖失败；下次启动会自动重试' }
        & $agentPython -m pip check
        if ($LASTEXITCODE -ne 0) { throw 'Agent 依赖检查未通过；下次启动会自动重试' }
        # The marker is written only after both commands succeed. An interrupted
        # install leaves no matching marker, even though python.exe already exists.
        $temporaryMarker = $dependencyMarker + '.tmp'
        [System.IO.File]::WriteAllText($temporaryMarker, $requirementsHash, [System.Text.Encoding]::ASCII)
        Move-Item -LiteralPath $temporaryMarker -Destination $dependencyMarker -Force
    }
    if ($Code) {
        if (-not $Server) { throw '首次注册需要 -Server 主控地址' }
        & $agentPython -m spiderfly_agent --data-dir $DataDir setup --server $Server --code $Code --name $Name
        if ($LASTEXITCODE -ne 0) { throw 'Agent 注册失败' }
    }
    $agentArguments = @('-m', 'spiderfly_agent', '--data-dir', $DataDir, 'run')
    if ($Dispatch) { $agentArguments += '--dispatch' }
    if (-not $Console) { $agentArguments += '--desktop' }
    & $agentPython @agentArguments
    if ($LASTEXITCODE -ne 0) { throw 'Agent 已退出，检查上方错误信息' }
} finally {
    Pop-Location
}
