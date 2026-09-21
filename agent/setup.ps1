param()

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$agentRoot = $PSScriptRoot
$managePath = Join-Path $agentRoot 'manage.ps1'
$dataRoot = Join-Path $env:LOCALAPPDATA 'SpiderFlyAgent'
$configPath = Join-Path $dataRoot 'config.json'
$setupLog = Join-Path $env:TEMP 'SpiderFlyAgent-setup.log'
$configured = Test-Path -LiteralPath $configPath -PathType Leaf

function Test-Python312 {
    try {
        $version = & python -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>$null
        return $LASTEXITCODE -eq 0 -and ([string]$version).Trim() -eq '3.12'
    } catch { return $false }
}
function Test-ServerUrl([string]$Value) {
    $uri = $null
    return [uri]::TryCreate($Value, [System.UriKind]::Absolute, [ref]$uri) -and $uri.Scheme -in @('http','https') -and $uri.Host -and $uri.AbsolutePath -eq '/' -and -not $uri.Query
}
function Quote-Ps([string]$Value) { return "'" + $Value.Replace("'", "''") + "'" }
function Set-StartupShortcut([bool]$Enabled, [bool]$Dispatch) {
    $startup = [Environment]::GetFolderPath('Startup')
    $shortcutPath = Join-Path $startup 'SpiderFly Agent.lnk'
    if (-not $Enabled) { if (Test-Path -LiteralPath $shortcutPath) { Remove-Item -LiteralPath $shortcutPath -Force }; return }
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $shortcut.Arguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$managePath`" Start" + $(if ($Dispatch) { ' -Dispatch' } else { '' })
    $shortcut.WorkingDirectory = $agentRoot
    $shortcut.WindowStyle = 7
    $shortcut.Description = '登录 Windows 后启动 SpiderFly Agent'
    $shortcut.Save()
}

$form = [System.Windows.Forms.Form]::new(); $form.Text = '安装并接入 SpiderFly Agent'; $form.ClientSize = [System.Drawing.Size]::new(600, 570); $form.StartPosition = 'CenterScreen'; $form.FormBorderStyle = 'FixedDialog'; $form.MaximizeBox = $false; $form.BackColor = [System.Drawing.Color]::White; $form.Font = [System.Drawing.Font]::new('Microsoft YaHei UI', 9)
$title = [System.Windows.Forms.Label]::new(); $title.Text = '接入这台电脑'; $title.Font = [System.Drawing.Font]::new('Microsoft YaHei UI', 17, [System.Drawing.FontStyle]::Bold); $title.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#221814'); $title.SetBounds(30,22,540,36)
$intro = [System.Windows.Forms.Label]::new(); $intro.Text = '从主控页面“宿主机”中复制主控地址和一次性接入码。安装完成后，需要管理员在网页中批准。'; $intro.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#656d65'); $intro.SetBounds(30,64,540,44)
$pythonState = [System.Windows.Forms.Label]::new(); $pythonState.SetBounds(30,112,540,24)

function New-Field([string]$labelText,[int]$top,[bool]$secret=$false) {
    $label=[System.Windows.Forms.Label]::new(); $label.Text=$labelText; $label.SetBounds(30,$top,540,22)
    $box=[System.Windows.Forms.TextBox]::new(); $box.SetBounds(30,($top+24),540,34); $box.UseSystemPasswordChar=$secret
    $form.Controls.Add($label); $form.Controls.Add($box); return $box
}
$server = New-Field '主控地址，例如 http://192.168.1.50:8000' 146
$code = New-Field '一次性接入码（只在首次接入时需要）' 218 $true
$machineName = New-Field '这台电脑的名称' 290
$machineName.Text = $env:COMPUTERNAME
$autoStart = [System.Windows.Forms.CheckBox]::new(); $autoStart.Text='登录 Windows 后自动启动 Agent'; $autoStart.Checked=$true; $autoStart.SetBounds(30,366,540,26)
$dispatch = [System.Windows.Forms.CheckBox]::new(); $dispatch.Text='启动后允许接收任务（主控页面也必须开启调度）'; $dispatch.Checked=$true; $dispatch.SetBounds(30,396,540,26)
$status = [System.Windows.Forms.Label]::new(); $status.Text='先检查主控连接，再安装。'; $status.ForeColor=[System.Drawing.ColorTranslator]::FromHtml('#656d65'); $status.SetBounds(30,438,540,46)
$check = [System.Windows.Forms.Button]::new(); $check.Text='检查连接'; $check.SetBounds(30,506,130,38); $check.FlatStyle='Flat'; $check.FlatAppearance.BorderColor=[System.Drawing.ColorTranslator]::FromHtml('#dfe3df'); $check.BackColor=[System.Drawing.Color]::White
$install = [System.Windows.Forms.Button]::new(); $install.Text=$(if($configured){'启动现有 Agent'}else{'安装并申请接入'}); $install.SetBounds(402,506,168,38); $install.FlatStyle='Flat'; $install.FlatAppearance.BorderSize=0; $install.BackColor=[System.Drawing.ColorTranslator]::FromHtml('#009139'); $install.ForeColor=[System.Drawing.Color]::White
$timer = [System.Windows.Forms.Timer]::new(); $timer.Interval=500
$script:process = $null

if ($configured) {
    try { $saved = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json; $server.Text=[string]$saved.server; $machineName.Text=[string]$saved.name } catch {}
    $server.Enabled=$false; $code.Enabled=$false; $machineName.Enabled=$false
    $intro.Text='这台电脑已经接入。可以重新启动 Agent，并更新登录 Windows 后的自动启动设置。'
}

$refresh = { if(Test-Python312){$pythonState.Text='✓ 已检测到 Python 3.12';$pythonState.ForeColor=[System.Drawing.ColorTranslator]::FromHtml('#007a31');$install.Enabled=$true}else{$pythonState.Text='× 未检测到 Python 3.12，请先安装并勾选“Add Python to PATH”';$pythonState.ForeColor=[System.Drawing.ColorTranslator]::FromHtml('#c34f3a');$install.Enabled=$false} }
$check.Add_Click({
    if(-not (Test-ServerUrl $server.Text.Trim())){$status.Text='主控地址格式不正确，请填写完整的 http://地址:端口。';$status.ForeColor=[System.Drawing.ColorTranslator]::FromHtml('#c34f3a');return}
    try { $status.Text='正在连接主控…';[System.Windows.Forms.Application]::DoEvents(); Invoke-WebRequest -UseBasicParsing -Uri ($server.Text.TrimEnd('/')+'/health') -TimeoutSec 5 | Out-Null; $status.Text='✓ 主控连接正常，可以继续安装。';$status.ForeColor=[System.Drawing.ColorTranslator]::FromHtml('#007a31') } catch { $status.Text='无法连接主控。请检查地址、两台电脑的网络以及主控防火墙。';$status.ForeColor=[System.Drawing.ColorTranslator]::FromHtml('#c34f3a') }
})
$timer.Add_Tick({
    if($script:process -and $script:process.HasExited){
        $timer.Stop(); $output=if(Test-Path -LiteralPath $setupLog){Get-Content -LiteralPath $setupLog -Raw -ErrorAction SilentlyContinue}else{''}
        if($script:process.ExitCode -eq 0){
            try { Set-StartupShortcut $autoStart.Checked $dispatch.Checked } catch { $output += "`r`n自动启动设置失败：$($_.Exception.Message)" }
            $status.Text=$(if($configured){'✓ Agent 已启动，可以在右下角托盘查看状态。'}else{'✓ 接入申请已提交，请让管理员在主控页面批准这台电脑。'});$status.ForeColor=[System.Drawing.ColorTranslator]::FromHtml('#007a31');$install.Text='完成';$install.Enabled=$false
        } else { $last=($output -split "`r?`n" | Where-Object {$_} | Select-Object -Last 1);$status.Text='未完成：'+$(if($last){$last}else{'请检查接入码、网络和安装日志。'});$status.ForeColor=[System.Drawing.ColorTranslator]::FromHtml('#c34f3a');$install.Enabled=$true }
        $check.Enabled=$true
    }
})
$install.Add_Click({
    if(-not $configured){
        if(-not (Test-ServerUrl $server.Text.Trim())){[System.Windows.Forms.MessageBox]::Show('请填写完整的主控地址。','检查主控地址')|Out-Null;return}
        if([string]::IsNullOrWhiteSpace($code.Text)){[System.Windows.Forms.MessageBox]::Show('请填写主控页面生成的一次性接入码。','检查接入码')|Out-Null;return}
        if([string]::IsNullOrWhiteSpace($machineName.Text)){[System.Windows.Forms.MessageBox]::Show('请填写这台电脑的名称。','检查电脑名称')|Out-Null;return}
    }
    if(Test-Path -LiteralPath $setupLog){Remove-Item -LiteralPath $setupLog -Force}
    $install.Enabled=$false;$check.Enabled=$false;$status.Text='正在准备 Agent 环境，首次安装可能需要几分钟…';$status.ForeColor=[System.Drawing.ColorTranslator]::FromHtml('#656d65')
    $command="& "+(Quote-Ps $managePath)+" Start"
    if(-not $configured){$command+=' -Server '+(Quote-Ps $server.Text.Trim())+' -Code '+(Quote-Ps $code.Text)+' -Name '+(Quote-Ps $machineName.Text.Trim())}
    if($dispatch.Checked){$command+=' -Dispatch'}
    $command+=" *>&1 | Out-File -LiteralPath "+(Quote-Ps $setupLog)+" -Encoding utf8"
    $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    $info=[Diagnostics.ProcessStartInfo]::new();$info.FileName='powershell.exe';$info.Arguments="-NoLogo -NoProfile -ExecutionPolicy Bypass -EncodedCommand $encoded";$info.UseShellExecute=$false;$info.CreateNoWindow=$true
    $script:process=[Diagnostics.Process]::Start($info);$timer.Start()
})

$form.Controls.AddRange(@($title,$intro,$pythonState,$autoStart,$dispatch,$status,$check,$install))
& $refresh
[void]$form.ShowDialog()
