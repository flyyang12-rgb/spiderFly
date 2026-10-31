param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('Start', 'Stop', 'Status', 'Logs', 'Connect', 'SetServer', 'Backup', 'Restore', 'Upgrade')]
    [string]$Action,
    [string]$Server,
    [string]$Code,
    [string]$Name = $env:COMPUTERNAME,
    [string]$DataDir = (Join-Path $env:LOCALAPPDATA 'SpiderFlyAgent'),
    [switch]$Dispatch,
    [switch]$Console,
    [switch]$Follow,
    [string]$BackupPath,
    [string]$PackagePath,
    [switch]$IncludeCaches,
    [switch]$ConfirmRestore,
    [string]$InstallDir,
    [string]$ProgramBackupPath,
    [string]$ResultPath
)

$ErrorActionPreference = 'Stop'
$agentRoot = if($InstallDir) { [IO.Path]::GetFullPath($InstallDir) } else { $PSScriptRoot }
if($InstallDir -and (-not(Test-Path -LiteralPath (Join-Path $agentRoot 'start.ps1')) -or
                    -not(Test-Path -LiteralPath (Join-Path $agentRoot 'spiderfly_agent/__init__.py')))) {
    throw '请选择原 Agent 安装目录'
}
$dataRoot = [System.IO.Path]::GetFullPath($DataDir)
$pidPath = Join-Path $dataRoot 'agent.pid.json'
$shutdownPath = Join-Path $dataRoot 'shutdown.request'
$logRoot = Join-Path $dataRoot 'logs'
$stdoutLog = Join-Path $logRoot 'agent.out.log'
$stderrLog = Join-Path $logRoot 'agent.error.log'

function Quote-Ps([string]$Value) { return "'" + $Value.Replace("'", "''") + "'" }

function Get-AgentProcess {
    if (-not (Test-Path -LiteralPath $pidPath -PathType Leaf)) { return $null }
    try {
        $record = Get-Content -LiteralPath $pidPath -Raw | ConvertFrom-Json
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$record.pid)" -ErrorAction Stop
        if (-not $process) { return $null }
        $expectedPython = [System.IO.Path]::GetFullPath((Join-Path $agentRoot '.venv\Scripts\python.exe'))
        $allowedExecutables = @($expectedPython)
        # A newly extracted program has no venv yet. The existing Agent may
        # still be running under the installed base Python from the old folder.
        # Keep the exact module and data-directory checks below for PID reuse.
        if ($env:LOCALAPPDATA) { $allowedExecutables += Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe' }
        if ($env:ProgramFiles) { $allowedExecutables += Join-Path $env:ProgramFiles 'Python312\python.exe' }
        $pathPython = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($pathPython) { $allowedExecutables += [IO.Path]::GetFullPath($pathPython.Source) }
        $venvConfig = Join-Path $agentRoot '.venv\pyvenv.cfg'
        if (Test-Path -LiteralPath $venvConfig -PathType Leaf) {
            $venvLines = Get-Content -LiteralPath $venvConfig -Encoding UTF8
            $baseExecutable = $venvLines | Where-Object { $_ -match '^executable\s*=\s*(.+)$' } | Select-Object -First 1
            if ($baseExecutable -match '^executable\s*=\s*(.+)$') {
                $allowedExecutables += [System.IO.Path]::GetFullPath($Matches[1].Trim())
            }
            # A venv created by another venv records that launcher as executable,
            # while Windows actually runs home/python.exe as the child process.
            $baseHome = $venvLines | Where-Object { $_ -match '^home\s*=\s*(.+)$' } | Select-Object -First 1
            if ($baseHome -match '^home\s*=\s*(.+)$') {
                $allowedExecutables += [System.IO.Path]::GetFullPath((Join-Path $Matches[1].Trim() 'python.exe'))
            }
        }
        $commandLine = [string]$process.CommandLine
        $dataArgument = '(?i)(?:^|\s)--data-dir\s+"?' + [regex]::Escape($dataRoot) + '"?(?:\s|$)'
        if (-not $process.ExecutablePath -or
                [System.IO.Path]::GetFullPath($process.ExecutablePath) -notin $allowedExecutables -or
                $commandLine -notmatch '(?i)(?:^|\s)-m\s+spiderfly_agent(?:\s|$)' -or
                $commandLine -notmatch $dataArgument) {
            # A process can disappear while CIM is reading its final properties.
            # Only confirmed absence is safe; an existing unknown PID still fails.
            if (-not (Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$record.pid)" -ErrorAction Stop)) { return $null }
            throw "PID $($record.pid) 已被其他进程占用，未执行操作；请人工核对 $pidPath"
        }
        return @{ Process = $process; Record = $record }
    } catch [Microsoft.PowerShell.Commands.ProcessCommandException] {
        return $null
    } catch [Microsoft.Management.Infrastructure.CimException] {
        throw '无法确认 Agent 进程状态，未执行操作；请检查本机 CIM 服务和权限'
    }
}

function Assert-Stopped {
    if (Get-AgentProcess) { throw 'Agent 仍在运行；请先执行 .\manage.ps1 Stop 并等待安全退出' }
}

function Show-Status {
    $state = Get-AgentProcess
    if ($state) {
        Write-Host "SpiderFly Agent 正在运行：PID $($state.Record.pid)，版本 $($state.Record.version)，协议 v$($state.Record.protocol_version)" -ForegroundColor Green
    } else {
        Write-Host 'SpiderFly Agent 未运行。' -ForegroundColor Yellow
    }
    Write-Host "数据目录：$dataRoot"
    Write-Host "日志：$stdoutLog"
}

function Start-Agent([switch]$SkipEnrollment) {
        if (Get-AgentProcess) { throw 'Agent 已在运行，不会重复启动' }
        New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
        if ($Console) {
            $arguments = @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $agentRoot 'start.ps1'), '-DataDir', $dataRoot)
            if ($Server) { $arguments += @('-Server', $Server) }
            if ($Code -and -not $SkipEnrollment) { $arguments += @('-Code', $Code) }
            if ($Name) { $arguments += @('-Name', $Name) }
            if ($Dispatch) { $arguments += '-Dispatch' }
            & powershell.exe @arguments -Console
            if ($LASTEXITCODE -ne 0) { throw 'Agent 启动失败' }; return
        }
        $command = '& ' + (Quote-Ps (Join-Path $agentRoot 'start.ps1')) + ' -DataDir ' + (Quote-Ps $dataRoot)
        if ($Server) { $command += ' -Server ' + (Quote-Ps $Server) }
        if ($Code -and -not $SkipEnrollment) { $command += ' -Code ' + (Quote-Ps $Code) }
        if ($Name) { $command += ' -Name ' + (Quote-Ps $Name) }
        if ($Dispatch) { $command += ' -Dispatch' }
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
        $child = Start-Process powershell.exe -ArgumentList @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded) `
            -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru
        $deadline = (Get-Date).AddMinutes($(if (Test-Path -LiteralPath (Join-Path $agentRoot '.venv\.dependencies.sha256')) { 1 } else { 10 }))
        do {
            Start-Sleep -Milliseconds 250
            $state = Get-AgentProcess
            if ($state) { Show-Status; return }
            if ($child.HasExited) {
                $lastError = if (Test-Path -LiteralPath $stderrLog) {
                    Get-Content -LiteralPath $stderrLog -ErrorAction SilentlyContinue |
                        Where-Object { $_ -match 'Agent 启动或运行失败:|安装 Agent 依赖失败|Agent 注册失败|创建 Agent 环境失败' } |
                        Select-Object -Last 1
                }
                throw "Agent 启动失败：$(if ($lastError) { $lastError } else { "请查看 $stderrLog" })"
            }
        } while ((Get-Date) -lt $deadline)
        throw "Agent 未在等待时间内就绪，请查看 $stderrLog 和 $stdoutLog"
}

function Stop-Agent {
        $state = Get-AgentProcess
        if (-not $state) { Write-Host 'Agent 已停止。'; return }
        New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null
        [System.IO.File]::WriteAllText($shutdownPath, [DateTimeOffset]::UtcNow.ToString('O'))
        $deadline = (Get-Date).AddSeconds(90)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Milliseconds 500
            if (-not (Get-AgentProcess)) {
                Write-Host 'Agent 已安全停止；未强制结束未知进程。' -ForegroundColor Green
                return
            }
        }
        throw 'Agent 未在 90 秒内安全退出；请从托盘停止/紧急接管并检查任务状态，脚本不会强杀进程'
}

function Save-ConnectResult($Value) {
    if ($ResultPath) {
        $temporary = $ResultPath + '.tmp'
        [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temporary -Destination $ResultPath -Force
    }
}

function Get-RegistrationStatus {
    $agentPython = Join-Path $agentRoot '.venv/Scripts/python.exe'
    Push-Location $agentRoot
    try {
        $output = & $agentPython -m spiderfly_agent --data-dir $dataRoot registration-status
        if ($LASTEXITCODE -ne 0) { throw '无法核对原登记，请检查 Agent 日志。' }
        return ($output | ConvertFrom-Json)
    } finally { Pop-Location }
}

switch ($Action) {
    'Start' { Start-Agent }
    'Stop' { Stop-Agent }
    'Connect' {
        try {
            # An interrupted install may already have python.exe but no imports.
            # Only a running Agent needs a preflight before preparing dependencies.
            $runtimeReady = (Test-Path -LiteralPath (Join-Path $agentRoot '.venv/Scripts/python.exe')) -and
                (Test-Path -LiteralPath (Join-Path $agentRoot '.venv/.dependencies.sha256'))
            if ($Code -and $runtimeReady -and (Get-AgentProcess)) {
                $before = Get-RegistrationStatus
                if ($before.can_reapply -eq $false) { throw '这台电脑仍有未结束任务或升级，请结束后再申请；原 Agent 保持运行。' }
                Stop-Agent
            }
            $prepareArguments = @('-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $agentRoot 'start.ps1'),'-DataDir',$dataRoot,'-PrepareOnly')
            if ($ResultPath) { $prepareArguments += @('-ResultPath',$ResultPath) }
            & powershell.exe @prepareArguments
            if ($LASTEXITCODE -ne 0) {
                $preparation = if ($ResultPath -and (Test-Path -LiteralPath $ResultPath)) { Get-Content -LiteralPath $ResultPath -Raw -Encoding UTF8 | ConvertFrom-Json }
                throw $(if ($preparation.message) { [string]$preparation.message } else { '准备 Agent 环境失败，请点击“查看安装详情”查看原因，然后重试。' })
            }
            if ($Code) {
                # Fresh download/partial install: prepare this program first,
                # then inspect the old Agent before asking it to stop.
                if (Get-AgentProcess) {
                    $before = Get-RegistrationStatus
                    if ($before.can_reapply -eq $false) { throw '这台电脑仍有未结束任务或升级，请结束后再申请；原 Agent 保持运行。' }
                    Stop-Agent
                }
                Save-ConnectResult @{ state='working'; message='正在向主控提交接入申请…' }
                $agentPython = Join-Path $agentRoot '.venv/Scripts/python.exe'
                Push-Location $agentRoot
                try {
                    & $agentPython -m spiderfly_agent --data-dir $dataRoot setup "--server=$Server" "--code=$Code" "--name=$Name"
                    if ($LASTEXITCODE -ne 0) { throw '接入申请未完成，请检查接入码和上方具体错误。' }
                } finally { Pop-Location }
            } else {
                $registration = Get-RegistrationStatus
                if ($registration.state -notin @('pending','approved')) {
                    $message = if ($registration.message) { [string]$registration.message } else { '原登记已撤销或拒绝，请填写新接入码重新申请。' }
                    Save-ConnectResult @{ state='requires_enrollment'; message=$message }
                    Write-Host $message
                    exit 2
                }
                if ($Name -and $Name -ne $registration.name) {
                    Save-ConnectResult @{ state='requires_enrollment'; message='修改电脑名称时，请填写新接入码重新申请。' }
                    exit 2
                }
            }
            Save-ConnectResult @{ state='working'; message='正在启动 Agent 并核对接入状态…' }
            if (-not (Get-AgentProcess)) { Start-Agent -SkipEnrollment }
            $registration = Get-RegistrationStatus
            if ($registration.state -notin @('pending','approved')) {
                throw $(if ($registration.message) { [string]$registration.message } else { '主控尚未确认本次接入，请填写新接入码重试。' })
            }
            Save-ConnectResult @{ state=$registration.state; host_id=$registration.host_id; name=$registration.name }
        } catch {
            Save-ConnectResult @{ state='error'; message=$_.Exception.Message }
            throw
        }
    }
    'Status' { Show-Status }
    'Logs' {
        if (-not (Test-Path -LiteralPath $stdoutLog)) { throw "尚无 Agent 日志：$stdoutLog" }
        Get-Content -LiteralPath $stdoutLog -Tail 200 -Wait:$Follow
        if (-not $Follow -and (Test-Path -LiteralPath $stderrLog)) {
            Write-Host "`n--- error log ---" -ForegroundColor Yellow
            Get-Content -LiteralPath $stderrLog -Tail 100
        }
    }
    'SetServer' {
        Assert-Stopped
        if (-not $Server) { throw '请用 -Server 指定新的 http(s)://主机:端口 地址' }
        $uri = $null
        if (-not [Uri]::TryCreate($Server, [UriKind]::Absolute, [ref]$uri) -or
                $uri.Scheme -notin @('http', 'https') -or -not $uri.Host -or
                $uri.UserInfo -or $uri.Query -or $uri.Fragment -or $uri.AbsolutePath -ne '/') {
            throw '主控地址必须是 http(s)://主机:端口，不带路径、查询或凭据'
        }
        $configPath = Join-Path $dataRoot 'config.json'
        if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { throw 'Agent 尚未注册，没有 config.json' }
        $journalPath = Join-Path $dataRoot 'journal.sqlite3'
        if (Test-Path -LiteralPath $journalPath -PathType Leaf) {
            $python = Join-Path $agentRoot '.venv\Scripts\python.exe'
            if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw '缺少 Agent Python，无法确认 journal 已全部回传' }
            $probe = 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); n=c.execute("select count(*) from runs where state != ?",("acked",)).fetchone()[0]; c.close(); raise SystemExit(0 if n==0 else 3)'
            $encodedProbe = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($probe))
            & $python -c 'import base64,sys;code=base64.b64decode(sys.argv.pop(1));exec(code)' $encodedProbe $journalPath
            if ($LASTEXITCODE -ne 0) { throw '仍有未回传或状态待确认运行，不能更改主控地址' }
        }
        $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $backup = $configPath + '.before-server-change-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
        Copy-Item -LiteralPath $configPath -Destination $backup
        $config.server = $Server.TrimEnd('/')
        $json = $config | ConvertTo-Json -Depth 5
        $temporary = $configPath + '.tmp'
        [System.IO.File]::WriteAllText($temporary, $json, [System.Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temporary -Destination $configPath -Force
        Write-Host "主控地址已更新为 $($config.server)；旧配置：$backup" -ForegroundColor Green
        Write-Host '仅适用于同一主控改变局域网地址。换主控请保留原数据目录并用新目录重新注册。'
    }
    'Backup' {
        Assert-Stopped
        if (-not (Test-Path -LiteralPath $dataRoot -PathType Container)) { throw "Agent 数据目录不存在：$dataRoot" }
        if (-not $BackupPath) {
            $BackupPath = Join-Path (Split-Path $dataRoot -Parent) ("SpiderFlyAgent-backup-{0}.zip" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
        }
        $BackupPath = [System.IO.Path]::GetFullPath($BackupPath)
        if (Test-Path -LiteralPath $BackupPath) { throw "备份文件已存在：$BackupPath" }
        $temporary = Join-Path ([System.IO.Path]::GetTempPath()) ('spiderfly-agent-backup-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path (Join-Path $temporary 'state') -Force | Out-Null
        $names = @('identity.json', 'config.json', 'journal.sqlite3', 'journal.sqlite3-wal', 'journal.sqlite3-shm', 'runs',
                   'controller-update-key.json','remote-update.json','upgrade.resume.json')
        if ($IncludeCaches) { $names += 'envs' }
        foreach ($name in $names) {
            $source = Join-Path $dataRoot $name
            if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination (Join-Path $temporary 'state') -Recurse -Force }
        }
        $manifest = @{
            format = 1; created_at = [DateTimeOffset]::UtcNow.ToString('O'); agent_version = '0.5.0'
            source_machine = $env:COMPUTERNAME; source_user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
            includes_environment_cache = [bool]$IncludeCaches
            files = @(Get-ChildItem -LiteralPath (Join-Path $temporary 'state') -File -Recurse | ForEach-Object {
                @{ path = $_.FullName.Substring((Join-Path $temporary 'state').Length + 1); sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }
            })
        }
        $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $temporary 'manifest.json') -Encoding UTF8
        Compress-Archive -Path (Join-Path $temporary '*') -DestinationPath $BackupPath -CompressionLevel Optimal
        Remove-Item -LiteralPath $temporary -Recurse -Force
        Write-Host "Agent 备份已创建：$BackupPath" -ForegroundColor Green
        Write-Host 'identity.json 受当前 Windows 账号 DPAPI 保护，只能在同一电脑和账号下恢复。'
    }
    'Restore' {
        Assert-Stopped
        if (-not $ConfirmRestore) { throw '恢复会写入 Agent 数据目录；核对目标后增加 -ConfirmRestore' }
        if (-not $BackupPath -or -not (Test-Path -LiteralPath $BackupPath -PathType Leaf)) { throw '请用 -BackupPath 指定有效备份 ZIP' }
        if ((Test-Path -LiteralPath $dataRoot -PathType Container) -and
                (Get-ChildItem -LiteralPath $dataRoot -Force | Select-Object -First 1)) {
            throw "目标数据目录非空，不会覆盖：$dataRoot；请先改名保留原目录"
        }
        $temporary = Join-Path ([System.IO.Path]::GetTempPath()) ('spiderfly-agent-restore-' + [guid]::NewGuid().ToString('N'))
        Expand-Archive -LiteralPath $BackupPath -DestinationPath $temporary
        $manifest = Get-Content -LiteralPath (Join-Path $temporary 'manifest.json') -Raw | ConvertFrom-Json
        foreach ($file in $manifest.files) {
            $path = Join-Path (Join-Path $temporary 'state') $file.path
            if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $file.sha256) {
                throw "备份校验失败：$($file.path)"
            }
        }
        New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null
        Copy-Item -Path (Join-Path $temporary 'state\*') -Destination $dataRoot -Recurse -Force
        Remove-Item -LiteralPath $temporary -Recurse -Force
        Write-Host 'Agent 数据已恢复。启动前确认仍是原 Windows 电脑与账号；否则重新注册新身份。' -ForegroundColor Green
    }
    'Upgrade' {
        Assert-Stopped
        if (-not $PackagePath -or -not (Test-Path -LiteralPath $PackagePath)) { throw '请用 -PackagePath 指定从主控下载并人工核对的 Agent ZIP 或目录' }
        $temporary = Join-Path ([System.IO.Path]::GetTempPath()) ('spiderfly-agent-upgrade-' + [guid]::NewGuid().ToString('N'))
        if (Test-Path -LiteralPath $PackagePath -PathType Leaf) {
            Expand-Archive -LiteralPath $PackagePath -DestinationPath $temporary
            $sourceRoot = Join-Path $temporary 'SpiderFlyAgent'
        } else { $sourceRoot = [System.IO.Path]::GetFullPath($PackagePath) }
        $required = @('README.md', 'requirements.txt', 'start.ps1', 'manage.ps1',
            'setup.ps1', 'install-python.ps1', '安装并接入Agent.bat', 'spiderfly_agent\__init__.py')
        foreach ($name in $required) { if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot $name) -PathType Leaf)) { throw "升级包缺少 $name" } }
        $archive = if($ProgramBackupPath) { [IO.Path]::GetFullPath($ProgramBackupPath) } else {
            Join-Path (Split-Path $agentRoot -Parent) ("SpiderFlyAgent-program-{0}-{1}.zip" -f (Get-Date -Format 'yyyyMMdd-HHmmss'),[guid]::NewGuid().ToString('N').Substring(0,8))
        }
        if(Test-Path -LiteralPath $archive){throw '程序备份文件已存在，不会覆盖'}
        $programNames = @('README.md', 'requirements.txt', 'start.ps1', 'manage.ps1',
            'setup.ps1', 'install-python.ps1', '安装并接入Agent.bat', 'spiderfly_agent',
            'upgrade.ps1', 'upgrade-core.ps1', 'upgrade-ui.ps1', 'upgrade-transport.py', '升级Agent.bat', 'remote-upgrade.ps1', 'controller-trust.json', 'update-manifest.json')
        $programFiles = $programNames | ForEach-Object { Join-Path $agentRoot $_ } | Where-Object { Test-Path -LiteralPath $_ }
        Compress-Archive -Path $programFiles -DestinationPath $archive -CompressionLevel Optimal
        foreach ($name in $programNames) {
            if(Test-Path -LiteralPath (Join-Path $sourceRoot $name)) {
                Copy-Item -LiteralPath (Join-Path $sourceRoot $name) -Destination $agentRoot -Recurse -Force
            }
        }
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Recurse -Force }
        Write-Host "Agent 程序已人工升级；旧程序备份：$archive" -ForegroundColor Green
        Write-Host '数据、机器身份和 journal 未改动。请执行 .\manage.ps1 Start，再检查 Status 与日志。'
    }
}
