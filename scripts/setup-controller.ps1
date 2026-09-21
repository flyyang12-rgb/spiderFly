param()

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$envPath = Join-Path $projectRoot '.env'
$startPath = Join-Path $projectRoot 'start.bat'
$lanPath = Join-Path $projectRoot 'configure_lan_access.ps1'

function Test-Python312 {
    try {
        $version = & python -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>$null
        return $LASTEXITCODE -eq 0 -and ([string]$version).Trim() -eq '3.12'
    } catch { return $false }
}

function Set-EnvValues([hashtable]$Values) {
    $lines = if (Test-Path -LiteralPath $envPath) { [System.Collections.Generic.List[string]](Get-Content -LiteralPath $envPath) } else { [System.Collections.Generic.List[string]]::new() }
    foreach ($name in $Values.Keys) {
        $replaced = $false
        for ($index = 0; $index -lt $lines.Count; $index++) {
            if ($lines[$index] -match ('^\s*' + [regex]::Escape($name) + '\s*=')) {
                $lines[$index] = "$name=$($Values[$name])"
                $replaced = $true
            }
        }
        if (-not $replaced) { $lines.Add("$name=$($Values[$name])") }
    }
    if (Test-Path -LiteralPath $envPath) {
        Copy-Item -LiteralPath $envPath -Destination ($envPath + '.before-setup-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    }
    [System.IO.File]::WriteAllLines($envPath, $lines, [System.Text.UTF8Encoding]::new($false))
}

$form = [System.Windows.Forms.Form]::new()
$form.Text = '安装 SpiderFly 主控'
$form.ClientSize = [System.Drawing.Size]::new(560, 440)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.BackColor = [System.Drawing.Color]::White
$form.Font = [System.Drawing.Font]::new('Microsoft YaHei UI', 9)

$title = [System.Windows.Forms.Label]::new(); $title.Text = '安装并启动主控'; $title.Font = [System.Drawing.Font]::new('Microsoft YaHei UI', 17, [System.Drawing.FontStyle]::Bold); $title.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#221814'); $title.SetBounds(28, 24, 500, 34)
$intro = [System.Windows.Forms.Label]::new(); $intro.Text = "只需确认端口和使用范围。程序会准备运行环境并自动打开管理页面。`r`n首次安装需要联网下载 Python 依赖，可能需要几分钟。"; $intro.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#656d65'); $intro.SetBounds(30, 66, 500, 48)
$pythonState = [System.Windows.Forms.Label]::new(); $pythonState.SetBounds(30, 126, 500, 24)
$portLabel = [System.Windows.Forms.Label]::new(); $portLabel.Text = '访问端口'; $portLabel.SetBounds(30, 166, 120, 22)
$port = [System.Windows.Forms.TextBox]::new(); $port.Text = '8000'; $port.SetBounds(30, 190, 500, 34)
$lan = [System.Windows.Forms.CheckBox]::new(); $lan.Text = '允许同一局域网的其他电脑访问（需要确认 Windows 管理员提示）'; $lan.Checked = $true; $lan.SetBounds(30, 244, 500, 28)
$hint = [System.Windows.Forms.Label]::new(); $hint.Text = '请只在可信的公司、家庭网络或自己的热点中开启，不要直接暴露到公网。'; $hint.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#757a75'); $hint.SetBounds(52, 274, 475, 36)
$status = [System.Windows.Forms.Label]::new(); $status.Text = '准备就绪。'; $status.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#656d65'); $status.SetBounds(30, 326, 500, 30)
$pythonButton = [System.Windows.Forms.Button]::new(); $pythonButton.Text = '下载 Python 3.12'; $pythonButton.SetBounds(30, 374, 150, 38); $pythonButton.FlatStyle = 'Flat'; $pythonButton.FlatAppearance.BorderColor = [System.Drawing.ColorTranslator]::FromHtml('#dfe3df'); $pythonButton.BackColor = [System.Drawing.Color]::White
$install = [System.Windows.Forms.Button]::new(); $install.Text = '安装并启动'; $install.SetBounds(380, 374, 150, 38); $install.FlatStyle = 'Flat'; $install.FlatAppearance.BorderSize = 0; $install.BackColor = [System.Drawing.ColorTranslator]::FromHtml('#009139'); $install.ForeColor = [System.Drawing.Color]::White

$refresh = {
    if (Test-Python312) { $pythonState.Text = '✓ 已检测到 64 位 Python 3.12'; $pythonState.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#007a31'); $install.Enabled = $true }
    else { $pythonState.Text = '× 未检测到 Python 3.12，请先下载安装并勾选“Add Python to PATH”'; $pythonState.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#c34f3a'); $install.Enabled = $false }
}
$pythonButton.Add_Click({ Start-Process 'https://www.python.org/downloads/release/python-31210/' })
$install.Add_Click({
    $parsedPort = 0
    if (-not [int]::TryParse($port.Text.Trim(), [ref]$parsedPort) -or $parsedPort -lt 1 -or $parsedPort -gt 65535) {
        [System.Windows.Forms.MessageBox]::Show('端口必须是 1 到 65535 之间的数字。', '请检查端口', 'OK', 'Warning') | Out-Null; return
    }
    try {
        $install.Enabled = $false; $status.Text = '正在保存配置…'; [System.Windows.Forms.Application]::DoEvents()
        Set-EnvValues @{ SPIDERFLY_PORT = $parsedPort; SPIDERFLY_HOST = $(if ($lan.Checked) { '0.0.0.0' } else { '127.0.0.1' }) }
        if ($lan.Checked) {
            $status.Text = '请在 Windows 提示中允许管理员权限…'; [System.Windows.Forms.Application]::DoEvents()
            $process = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $lanPath + '"'),'-Port',$parsedPort)
            if ($process.ExitCode -ne 0) { throw '局域网访问配置未完成；可以取消勾选后仅在本机使用，或重新尝试。' }
        }
        $status.Text = '正在启动，随后会自动打开浏览器…'; [System.Windows.Forms.Application]::DoEvents()
        Start-Process cmd.exe -WorkingDirectory $projectRoot -ArgumentList @('/k', ('"' + $startPath + '"'))
        $form.Close()
    } catch {
        $status.Text = '未完成：' + $_.Exception.Message; $status.ForeColor = [System.Drawing.ColorTranslator]::FromHtml('#c34f3a'); $install.Enabled = $true
    }
})

$form.Controls.AddRange(@($title,$intro,$pythonState,$portLabel,$port,$lan,$hint,$status,$pythonButton,$install))
& $refresh
[void]$form.ShowDialog()
