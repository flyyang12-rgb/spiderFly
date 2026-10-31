param(
    [string]$Server,
    [string]$Code,
    [string]$Name = $env:COMPUTERNAME,
    [string]$DataDir = (Join-Path $env:LOCALAPPDATA 'SpiderFlyAgent'),
    [switch]$Dispatch,
    [switch]$Console,
    [switch]$PrepareOnly,
    [string]$ResultPath
)
$ErrorActionPreference = 'Stop'

function Set-PreparationStatus([string]$State, [string]$Message) {
    if ($ResultPath) {
        $value = @{ state=$State; message=$Message; stage=$script:preparationStage }
        $temporary = $ResultPath + '.tmp'
        [IO.File]::WriteAllText($temporary, ($value | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temporary -Destination $ResultPath -Force
    }
}

function Invoke-PreparationStep([string]$Executable, [string[]]$Arguments, [string]$FailureMessage) {
    # Read native streams directly. Windows PowerShell 5.1 otherwise turns pip's
    # stderr notices into NativeCommandError when the wizard redirects its output.
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $Executable
    $info.WorkingDirectory = $PSScriptRoot
    $info.Arguments = (@($Arguments | ForEach-Object {
        '"' + [regex]::Replace([regex]::Replace($_, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
    }) -join ' ')
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = [Text.Encoding]::UTF8
    $info.StandardErrorEncoding = [Text.Encoding]::UTF8
    $info.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
    $child = [Diagnostics.Process]::Start($info)
    try {
        # Drain both pipes concurrently, including large pip errors, without
        # making stderr itself a PowerShell error or hiding the real exit code.
        $stdout = $child.StandardOutput.ReadToEndAsync()
        $stderr = $child.StandardError.ReadToEndAsync()
        $child.WaitForExit()
        $lines = @(($stdout.Result + "`n" + $stderr.Result) -split "`r?`n" | Where-Object { $_.Trim() })
        $lines | Write-Output
        if ($child.ExitCode -ne 0) {
            $reason = $lines | Where-Object { $_ -match '(?i)^ERROR:|Error:|Exception:|conflict|requires ' } | Select-Object -Last 1
            if (-not $reason) { $reason = $lines | Where-Object { $_ -notmatch '^\[notice\]' } | Select-Object -Last 1 }
            if ($reason -and $reason.Length -gt 220) { $reason = $reason.Substring(0,220) + '…' }
            throw "$FailureMessage（退出码 $($child.ExitCode)）$(if($reason){'：'+$reason})。修正原因后直接点击接入并启动重试。"
        }
    } finally { $child.Dispose() }
}

function Get-Python312Command {
    $candidates = @()
    if ($env:LOCALAPPDATA) { $candidates += Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe' }
    if ($env:ProgramFiles) { $candidates += Join-Path $env:ProgramFiles 'Python312\python.exe' }
    $candidates += 'python'
    foreach ($candidate in $candidates) {
        try {
            & $candidate -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3,12) and sys.maxsize > 2**32 else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        } catch { }
    }
    throw '未检测到 64 位 Python 3.12；请在这台电脑安装后重试'
}

Push-Location $PSScriptRoot
try {
    $script:preparationStage = 'environment'
    $agentPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
    $createdEnvironment = $false
    if (-not (Test-Path -LiteralPath $agentPython)) {
        $systemPython = Get-Python312Command
        Set-PreparationStatus 'working' '正在创建 Agent 环境…'
        Invoke-PreparationStep $systemPython @('-m','venv',(Join-Path $PSScriptRoot '.venv')) '创建 Agent 环境失败'
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
        $script:preparationStage = 'dependencies'
        Set-PreparationStatus 'working' '正在安装 Agent 所需依赖，首次安装可能需要几分钟…'
        Invoke-PreparationStep $agentPython @('-m','pip','install','-r',$requirementsPath) '安装 Agent 依赖失败，请检查网络或代理'
        $script:preparationStage = 'dependency_check'
        Set-PreparationStatus 'working' '正在检查 Agent 依赖…'
        Invoke-PreparationStep $agentPython @('-m','pip','check') 'Agent 依赖检查未通过'
        # The marker is written only after both commands succeed. An interrupted
        # install leaves no matching marker, even though python.exe already exists.
        $temporaryMarker = $dependencyMarker + '.tmp'
        [System.IO.File]::WriteAllText($temporaryMarker, $requirementsHash, [System.Text.Encoding]::ASCII)
        Move-Item -LiteralPath $temporaryMarker -Destination $dependencyMarker -Force
    }
    if ($PrepareOnly) { exit 0 }
    if ($Code) {
        if (-not $Server) { throw '首次注册需要 -Server 主控地址' }
        & $agentPython -m spiderfly_agent --data-dir $DataDir setup "--server=$Server" "--code=$Code" "--name=$Name"
        if ($LASTEXITCODE -ne 0) { throw 'Agent 注册失败' }
    }
    $agentArguments = @('-m', 'spiderfly_agent', '--data-dir', $DataDir, 'run')
    if ($Dispatch) { $agentArguments += '--dispatch' }
    if (-not $Console) { $agentArguments += '--desktop' }
    & $agentPython @agentArguments
    if ($LASTEXITCODE -ne 0) { throw 'Agent 已退出，检查上方错误信息' }
} catch {
    Set-PreparationStatus 'error' $_.Exception.Message
    # The caller records this as text, then uses the exit code and result JSON.
    # Do not turn a useful pip error into a second NativeCommandError upstream.
    Write-Output $_.Exception.Message
    exit 1
} finally {
    Pop-Location
}
