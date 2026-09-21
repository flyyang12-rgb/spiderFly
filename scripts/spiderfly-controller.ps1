param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('Start', 'Stop', 'Status', 'Logs', 'Backup', 'Restore', 'Upgrade')]
    [string]$Action,
    [switch]$Follow,
    [string]$BackupPath,
    [switch]$ConfirmRestore
)

$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$envFile = Join-Path $projectRoot '.env'

function Read-Settings([string]$Path = $envFile) {
    $settings = @{}
    if (Test-Path -LiteralPath $Path) {
        foreach ($line in Get-Content -LiteralPath $Path) {
            if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
            $name, $value = $line -split '=', 2
            $settings[$name.Trim()] = $value.Trim().Trim('"').Trim("'")
        }
    }
    return $settings
}

function Resolve-ConfiguredPath([hashtable]$settings, [string]$name, [string]$fallback) {
    $processValue = [Environment]::GetEnvironmentVariable($name, 'Process')
    $value = if ($processValue) { $processValue } elseif ($settings.ContainsKey($name)) { $settings[$name] } else { $fallback }
    if ([System.IO.Path]::IsPathRooted($value)) { return [System.IO.Path]::GetFullPath($value) }
    return [System.IO.Path]::GetFullPath((Join-Path $projectRoot $value))
}

$settings = Read-Settings
$dataRoot = Resolve-ConfiguredPath $settings 'SPIDERFLY_DATA_DIR' 'data'
$appsRoot = Resolve-ConfiguredPath $settings 'SPIDERFLY_APPS_DIR' (Join-Path $dataRoot 'apps')
$envsRoot = Resolve-ConfiguredPath $settings 'SPIDERFLY_ENVS_DIR' (Join-Path $dataRoot 'envs')
$workRoot = Resolve-ConfiguredPath $settings 'SPIDERFLY_WORK_DIR' '共享工作区'
$logPath = Join-Path $dataRoot 'logs\spiderfly.log'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'

function Get-ControllerProcesses {
    $launcherPath = [System.IO.Path]::GetFullPath((Join-Path $projectRoot 'SpiderFly.exe'))
    $items = @()
    foreach ($process in Get-CimInstance Win32_Process) {
        if ($process.ExecutablePath) {
            try { $executable = [System.IO.Path]::GetFullPath($process.ExecutablePath) } catch { continue }
            if ($executable -eq $launcherPath) { $items += $process; continue }
            if ($executable -eq $pythonPath -and $process.CommandLine -match 'uvicorn' -and $process.CommandLine -match 'app\.main:app') {
                $items += $process
            }
        }
    }
    return @($items | Sort-Object ProcessId -Unique)
}

function Assert-Stopped {
    $running = @(Get-ControllerProcesses)
    if ($running.Count -gt 0) { throw "主控仍在运行（PID：$($running.ProcessId -join ', ')）；请先执行 Stop" }
}

function Invoke-DatabaseCheck([switch]$RequireIdle, [string]$Root = $dataRoot) {
    if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw "缺少平台 Python：$pythonPath" }
    $databasePath = Join-Path $Root 'spiderfly.db'
    if (-not (Test-Path -LiteralPath $databasePath -PathType Leaf)) { return }
    $probe = @'
import sqlite3, sys
db, require_idle = sys.argv[1], sys.argv[2] == "1"
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
try:
    result = con.execute("PRAGMA quick_check").fetchone()[0]
    if result != "ok": raise SystemExit("数据库 quick_check 失败: " + str(result))
    if require_idle:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        busy = []
        if "executions" in names:
            busy += list(con.execute("SELECT 'execution', id, status FROM executions WHERE status IN ('pending','running','stopping')"))
        if "remote_runs" in names:
            busy += list(con.execute("SELECT 'remote', id, status FROM remote_runs WHERE status IN ('preparing','running','stopping','uncertain')"))
        if busy: raise SystemExit("仍有未收尾运行: " + repr(busy[:20]))
finally:
    con.close()
'@
    $encodedProbe = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($probe))
    & $pythonPath -X utf8 -c 'import base64,sys;code=base64.b64decode(sys.argv.pop(1));exec(code)' $encodedProbe $databasePath $(if ($RequireIdle) { '1' } else { '0' })
    if ($LASTEXITCODE -ne 0) { throw '数据库检查未通过' }
}

function New-ControllerBackup([string]$Destination) {
    Assert-Stopped
    Invoke-DatabaseCheck -RequireIdle
    if (-not $Destination) {
        $Destination = Join-Path (Split-Path $projectRoot -Parent) ("SpiderFly-controller-backup-{0}.zip" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
    }
    $Destination = [System.IO.Path]::GetFullPath($Destination)
    if (Test-Path -LiteralPath $Destination) { throw "备份文件已存在：$Destination" }
    $temporary = Join-Path ([System.IO.Path]::GetTempPath()) ('spiderfly-controller-backup-' + [guid]::NewGuid().ToString('N'))
    $payload = Join-Path $temporary 'payload'
    New-Item -ItemType Directory -Force -Path $payload | Out-Null
    $entries = @()
    $roots = @(
        @{ role = 'data'; path = $dataRoot }, @{ role = 'apps'; path = $appsRoot },
        @{ role = 'envs'; path = $envsRoot }, @{ role = 'work'; path = $workRoot }
    )
    $copied = @{}
    foreach ($root in $roots) {
        $resolved = [System.IO.Path]::GetFullPath($root.path).TrimEnd('\')
        $parent = $roots | Where-Object { $_.role -ne $root.role -and $resolved.StartsWith(([System.IO.Path]::GetFullPath($_.path).TrimEnd('\') + '\'), [System.StringComparison]::OrdinalIgnoreCase) } | Select-Object -First 1
        if ($parent) {
            $entries += @{ role = $root.role; included_by = $parent.role; configured_path = $resolved }
            continue
        }
        if (-not (Test-Path -LiteralPath $resolved -PathType Container)) { continue }
        $slot = 'root-' + $root.role
        Copy-Item -LiteralPath $resolved -Destination (Join-Path $payload $slot) -Recurse -Force
        $entries += @{ role = $root.role; slot = $slot; configured_path = $resolved }
        $copied[$resolved] = $true
    }
    if (Test-Path -LiteralPath $envFile -PathType Leaf) { Copy-Item -LiteralPath $envFile -Destination (Join-Path $payload 'settings.env') }
    $files = @(Get-ChildItem -LiteralPath $payload -File -Recurse | ForEach-Object {
        @{ path = $_.FullName.Substring($payload.Length + 1); sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }
    })
    @{ format = 1; created_at = [DateTimeOffset]::UtcNow.ToString('O'); entries = $entries; files = $files } |
        ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $temporary 'manifest.json') -Encoding UTF8
    Compress-Archive -Path (Join-Path $temporary '*') -DestinationPath $Destination -CompressionLevel Optimal
    Remove-Item -LiteralPath $temporary -Recurse -Force
    Write-Host "主控备份已创建：$Destination" -ForegroundColor Green
    Write-Host '备份含数据库、版本文件、产物和配置指向的运行目录；模型密钥仍受原 Windows 账号 DPAPI 约束。'
}

switch ($Action) {
    'Start' {
        if (@(Get-ControllerProcesses).Count -gt 0) { throw 'SpiderFly 主控已运行，不会重复启动' }
        $launcher = Join-Path $projectRoot 'SpiderFly.exe'
        if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) { throw "缺少启动器：$launcher" }
        Start-Process -FilePath $launcher -WorkingDirectory $projectRoot
        Write-Host '已发出主控启动请求；请执行 Status 并查看健康检查与日志。' -ForegroundColor Green
    }
    'Stop' {
        $running = @(Get-ControllerProcesses)
        if ($running.Count -eq 0) { Write-Host '主控已停止。'; exit 0 }
        Invoke-DatabaseCheck -RequireIdle
        foreach ($process in ($running | Sort-Object { if ([System.IO.Path]::GetFileName($_.ExecutablePath) -eq 'SpiderFly.exe') { 0 } else { 1 } })) {
            Stop-Process -Id $process.ProcessId -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 2
        if (@(Get-ControllerProcesses).Count -gt 0) { throw '主控进程未完全退出，请核对日志和进程归属' }
        Write-Host '主控已停止；停止前数据库确认无执行中、停止中或状态待确认任务。' -ForegroundColor Green
    }
    'Status' {
        $running = @(Get-ControllerProcesses)
        if ($running.Count) { Write-Host "主控进程：$($running.ProcessId -join ', ')" -ForegroundColor Green } else { Write-Host '主控未运行。' -ForegroundColor Yellow }
        $port = if ($env:SPIDERFLY_PORT) { $env:SPIDERFLY_PORT } elseif ($settings.ContainsKey('SPIDERFLY_PORT')) { $settings['SPIDERFLY_PORT'] } else { '8000' }
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 3
            Write-Host "健康检查：$($health | ConvertTo-Json -Compress)" -ForegroundColor Green
        } catch { Write-Host '健康检查不可用。' -ForegroundColor Yellow }
        Write-Host "数据目录：$dataRoot"
        Write-Host "日志：$logPath"
    }
    'Logs' {
        if (-not (Test-Path -LiteralPath $logPath -PathType Leaf)) { throw "尚无主控日志：$logPath" }
        Get-Content -LiteralPath $logPath -Tail 200 -Wait:$Follow
    }
    'Backup' { New-ControllerBackup $BackupPath }
    'Restore' {
        Assert-Stopped
        if (-not $ConfirmRestore) { throw '恢复会写入当前配置指向的目录；核对目标后增加 -ConfirmRestore' }
        if (-not $BackupPath -or -not (Test-Path -LiteralPath $BackupPath -PathType Leaf)) { throw '请用 -BackupPath 指定有效备份 ZIP' }
        $temporary = Join-Path ([System.IO.Path]::GetTempPath()) ('spiderfly-controller-restore-' + [guid]::NewGuid().ToString('N'))
        Expand-Archive -LiteralPath $BackupPath -DestinationPath $temporary
        $manifest = Get-Content -LiteralPath (Join-Path $temporary 'manifest.json') -Raw | ConvertFrom-Json
        $payload = Join-Path $temporary 'payload'
        foreach ($file in $manifest.files) {
            $path = Join-Path $payload $file.path
            if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $file.sha256) { throw "备份校验失败：$($file.path)" }
        }
        $backupSettingsPath = Join-Path $payload 'settings.env'
        $restoreSettings = $settings
        if (Test-Path -LiteralPath $backupSettingsPath) {
            if ((Test-Path -LiteralPath $envFile) -and
                    (Get-FileHash -LiteralPath $backupSettingsPath -Algorithm SHA256).Hash -ne
                    (Get-FileHash -LiteralPath $envFile -Algorithm SHA256).Hash) {
                throw '.env 与备份配置不同，不会猜测路径或覆盖；请人工统一配置后重试'
            }
            if (-not (Test-Path -LiteralPath $envFile)) { $restoreSettings = Read-Settings $backupSettingsPath }
        }
        $restoreDataRoot = Resolve-ConfiguredPath $restoreSettings 'SPIDERFLY_DATA_DIR' 'data'
        $restoreAppsRoot = Resolve-ConfiguredPath $restoreSettings 'SPIDERFLY_APPS_DIR' (Join-Path $restoreDataRoot 'apps')
        $restoreEnvsRoot = Resolve-ConfiguredPath $restoreSettings 'SPIDERFLY_ENVS_DIR' (Join-Path $restoreDataRoot 'envs')
        $restoreWorkRoot = Resolve-ConfiguredPath $restoreSettings 'SPIDERFLY_WORK_DIR' '共享工作区'
        $targets = @{ data = $restoreDataRoot; apps = $restoreAppsRoot; envs = $restoreEnvsRoot; work = $restoreWorkRoot }
        foreach ($entry in $manifest.entries) {
            if (-not $entry.slot) { continue }
            $target = $targets[$entry.role]
            if ((Test-Path -LiteralPath $target -PathType Container) -and (Get-ChildItem -LiteralPath $target -Force | Select-Object -First 1)) {
                throw "恢复目标非空，不会覆盖：$target；请先改名保留原目录"
            }
            New-Item -ItemType Directory -Force -Path $target | Out-Null
            Copy-Item -Path (Join-Path (Join-Path $payload $entry.slot) '*') -Destination $target -Recurse -Force
        }
        if ((Test-Path -LiteralPath $backupSettingsPath) -and -not (Test-Path -LiteralPath $envFile)) {
            Copy-Item -LiteralPath $backupSettingsPath -Destination $envFile
        }
        Invoke-DatabaseCheck -Root $restoreDataRoot
        Remove-Item -LiteralPath $temporary -Recurse -Force
        Write-Host '主控运行资料已恢复并通过数据库 quick_check；尚未启动，也未运行真实任务。' -ForegroundColor Green
    }
    'Upgrade' {
        Assert-Stopped
        Invoke-DatabaseCheck -RequireIdle
        $dirty = git -C $projectRoot status --porcelain
        if ($LASTEXITCODE -ne 0 -or $dirty) { throw '工作区不是干净 Git 发布目录；不会自动合并或覆盖本机修改' }
        New-ControllerBackup $BackupPath
        git -C $projectRoot pull --ff-only
        if ($LASTEXITCODE -ne 0) { throw 'Git 快进升级失败；数据备份已保留，主控未重启' }
        Write-Host '源码已快进升级。请人工检查变更，再执行 start.bat 更新依赖/构建，最后用 Start 启动。' -ForegroundColor Green
    }
}
