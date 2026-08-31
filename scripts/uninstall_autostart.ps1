[CmdletBinding(SupportsShouldProcess = $true)]
param()

$ErrorActionPreference = 'Stop'
$taskPath = '\'
$taskName = 'SpiderFly Host Autostart 04C5438E'
$ownerMarker = 'SpiderFly.Autostart.Owner.04C5438E-83B3-49D8-8868-630EEDC79E5C'
Import-Module ScheduledTasks -ErrorAction Stop

$task = Get-ScheduledTask `
    -TaskPath $taskPath `
    -TaskName $TaskName `
    -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host '[SpiderFly] 没有找到登录自启任务，无需删除。'
    return
}
if ($task.Description -notlike "$ownerMarker*") {
    throw "找到同名计划任务 [$TaskName]，但它不是本启动器创建的任务；为避免误删已停止操作。"
}

$target = 'Windows 登录自启任务 [{0}]' -f $TaskName
if ($PSCmdlet.ShouldProcess($target, '删除')) {
    try {
        Unregister-ScheduledTask `
            -TaskPath $taskPath `
            -TaskName $TaskName `
            -Confirm:$false
    } catch {
        throw "登录自启删除失败。原始原因：$($_.Exception.Message)"
    }
    Write-Host '[SpiderFly] 登录自启已经删除。' -ForegroundColor Green
    Write-Host '当前已经运行的 SpiderFly 不会被强行结束；下次登录时不再自动启动。'
}
