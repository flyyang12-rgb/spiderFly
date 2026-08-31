param(
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
$ruleName = 'SpiderFly Shared Panel (TCP 8000)'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$isAdministrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdministrator) {
    Write-Host '[SpiderFly] 正在请求 Windows 管理员权限……' -ForegroundColor Cyan
    $arguments = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', ('"{0}"' -f $PSCommandPath),
        '-Port', $Port
    )
    $elevated = Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments -Wait -PassThru
    exit $elevated.ExitCode
}

$profiles = @(Get-NetConnectionProfile | Where-Object {
    $_.IPv4Connectivity -in @('Internet', 'LocalNetwork')
})

if (-not $profiles) {
    throw '没有找到已连接的 IPv4 网络。请连接公司或家庭网络后重试。'
}

Write-Host ''
Write-Host '[SpiderFly] 当前连接的网络：' -ForegroundColor Cyan
$profiles | Format-Table -AutoSize InterfaceAlias, Name, NetworkCategory, IPv4Connectivity

$publicProfiles = @($profiles | Where-Object { $_.NetworkCategory -eq 'Public' })
if ($publicProfiles) {
    Write-Host '只有在当前网络是可信的公司、家庭或自己的热点时才应继续。' -ForegroundColor Yellow
    $answer = Read-Host '要把上面的当前网络设为“专用网络”并允许同一局域网访问吗？输入 Y 继续'
    if ($answer -notmatch '^[Yy]$') {
        Write-Host '已取消，没有修改网络或防火墙。'
        exit 1
    }

    foreach ($profile in $publicProfiles) {
        Set-NetConnectionProfile -InterfaceIndex $profile.InterfaceIndex -NetworkCategory Private
    }
}

$existingRule = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($existingRule) {
    $existingRule | Remove-NetFirewallRule
}

New-NetFirewallRule `
    -DisplayName $ruleName `
    -Description 'Allow authenticated SpiderFly access from the current local subnet.' `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort $Port `
    -RemoteAddress LocalSubnet `
    -Profile Private `
    -EdgeTraversalPolicy Block | Out-Null

$addresses = @(Get-NetIPAddress -AddressFamily IPv4 -AddressState Preferred | Where-Object {
    $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*'
} | Select-Object -ExpandProperty IPAddress -Unique)

Write-Host ''
Write-Host '[SpiderFly] 局域网访问已经开启。' -ForegroundColor Green
foreach ($address in $addresses) {
    Write-Host ("  http://{0}:{1}" -f $address, $Port) -ForegroundColor Green
}
Write-Host '只允许同一本地子网访问；没有创建公网端口映射。'
