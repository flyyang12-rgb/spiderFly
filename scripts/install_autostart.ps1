[CmdletBinding(SupportsShouldProcess = $true)]
param()

$ErrorActionPreference = 'Stop'
$taskPath = '\'
$taskName = 'SpiderFly Host Autostart 04C5438E'
$ownerMarker = 'SpiderFly.Autostart.Owner.04C5438E-83B3-49D8-8868-630EEDC79E5C'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$launcherPath = Join-Path $projectRoot 'SpiderFly.exe'

if (-not (Test-Path -LiteralPath $launcherPath -PathType Leaf)) {
    throw "没有找到 SpiderFly.exe。请先运行 scripts\build_launcher.ps1：$launcherPath"
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$userId = $identity.Name
$expectedUserSid = $identity.User.Value
$sessionId = (Get-Process -Id $PID).SessionId
if ($identity.IsSystem -or $sessionId -eq 0) {
    throw '请在固定的 RPA Windows 账号登录桌面后运行本脚本，不能以 SYSTEM 或后台会话安装。'
}

Import-Module ScheduledTasks -ErrorAction Stop

function Resolve-AccountSid {
    param([string]$AccountName)

    if ([string]::IsNullOrWhiteSpace($AccountName)) {
        return $null
    }
    try {
        if ($AccountName -like 'S-1-*') {
            return ([Security.Principal.SecurityIdentifier]::new($AccountName)).Value
        }
        return ([Security.Principal.NTAccount]::new($AccountName)).Translate(
            [Security.Principal.SecurityIdentifier]
        ).Value
    } catch {
        return $null
    }
}

$action = New-ScheduledTaskAction `
    -Execute $launcherPath `
    -Argument '--startup' `
    -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$principal = New-ScheduledTaskPrincipal `
    -UserId $userId `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$definition = New-ScheduledTask `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "$ownerMarker | SpiderFly 在固定 RPA 用户登录后后台启动。"

$existing = Get-ScheduledTask `
    -TaskPath $taskPath `
    -TaskName $TaskName `
    -ErrorAction SilentlyContinue
if ($existing -and $existing.Description -notlike "$ownerMarker*") {
    throw "Windows 根任务目录中已经存在同名的非 SpiderFly 任务 [$TaskName]，为避免覆盖已停止安装。"
}

$target = 'Windows 登录自启任务 [{0}]（用户：{1}）' -f $TaskName, $userId
if ($PSCmdlet.ShouldProcess($target, '创建或更新并立即启动')) {
    $registeredByThisRun = $false
    try {
        Register-ScheduledTask `
            -TaskPath $taskPath `
            -TaskName $TaskName `
            -InputObject $definition `
            -Force | Out-Null
        $registeredByThisRun = $true
    } catch {
        throw "登录自启安装失败。公司策略限制时请联系 IT 管理员。原始原因：$($_.Exception.Message)"
    }

    $registered = Get-ScheduledTask `
        -TaskPath $taskPath `
        -TaskName $TaskName `
        -ErrorAction Stop
    $registeredAction = @($registered.Actions)
    $registeredTrigger = @($registered.Triggers)
    $registeredPrincipalSid = Resolve-AccountSid $registered.Principal.UserId
    $registeredTriggerSid = Resolve-AccountSid $registeredTrigger[0].UserId
    $validDefinition = `
        $registered.Description -like "$ownerMarker*" -and `
        $registeredPrincipalSid -eq $expectedUserSid -and `
        $registered.Principal.LogonType -in @('Interactive', 'InteractiveToken') -and `
        $registeredAction.Count -eq 1 -and `
        $registeredAction[0].Execute -eq $launcherPath -and `
        $registeredAction[0].Arguments -eq '--startup' -and `
        $registeredAction[0].WorkingDirectory -eq $projectRoot -and `
        $registeredTrigger.Count -eq 1 -and `
        $registeredTriggerSid -eq $expectedUserSid
    if (-not $validDefinition) {
        if ($registeredByThisRun -and $registered.Description -like "$ownerMarker*") {
            Unregister-ScheduledTask `
                -TaskPath $taskPath `
                -TaskName $taskName `
                -Confirm:$false `
                -ErrorAction SilentlyContinue
        }
        throw '自启任务注册后的安全校验未通过，已经撤销本次安装。'
    }

    if ($registered.State -ne 'Running') {
        try {
            Start-ScheduledTask -TaskPath $taskPath -TaskName $taskName
        } catch {
            throw "自启任务已经安全注册，但本次立即启动失败；下次登录仍会自动启动。原始原因：$($_.Exception.Message)"
        }
    }

    Write-Host '[SpiderFly] 登录自启已经安装。' -ForegroundColor Green
    Write-Host ("  固定用户：{0}" -f $userId) -ForegroundColor Green
    Write-Host '  运行方式：仅在该用户登录桌面后运行' -ForegroundColor Green
    Write-Host '  自启模式不会自动弹出浏览器。' -ForegroundColor Green
}
