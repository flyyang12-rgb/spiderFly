function New-AgentUpgradeJob {
    $root=Join-Path $env:LOCALAPPDATA 'SpiderFlyAgent-upgrades'
    $job=Join-Path $root ((Get-Date -Format 'yyyyMMdd-HHmmss')+'-'+[guid]::NewGuid().ToString('N').Substring(0,8))
    New-Item -ItemType Directory -Force -Path $job|Out-Null
    $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls.exe $job /inheritance:r /grant:r "*$($sid):(OI)(CI)F" '*S-1-5-18:(OI)(CI)F'|Out-Null
    if($LASTEXITCODE -ne 0){throw '无法创建私有升级备份目录'}
    return $job
}

function Start-AgentUpgradeProcess($Context){
    if($Context.busy){return}
    $resume=[bool]$Context.resumePending
    if(-not $resume){
        if([string]::IsNullOrWhiteSpace($Context.install.Text) -or -not(Test-Path -LiteralPath $Context.install.Text -PathType Container)){$Context.status.Text='请选择原 Agent 安装目录';return}
        if([string]::IsNullOrWhiteSpace($Context.package.Text) -or -not(Test-Path -LiteralPath $Context.package.Text)){$Context.status.Text='请选择从主控下载的升级包';return}
        $Context.job=New-AgentUpgradeJob
    }
    $command='[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); $ProgressPreference=''SilentlyContinue''; . '+(Quote-UpgradeLiteral (Join-Path $Context.sourceRoot 'upgrade-core.ps1'))+'; Invoke-AgentUpgrade'
    $command+=' -InstallDir '+(Quote-UpgradeLiteral $Context.install.Text)+' -DataDir '+(Quote-UpgradeLiteral $Context.data.Text)
    $command+=' -PackagePath '+(Quote-UpgradeLiteral $Context.package.Text)+' -JobDir '+(Quote-UpgradeLiteral $Context.job)
    if($resume){$command+=' -ResumeOnly'}
    $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    $Context.outputPath=Join-Path $Context.job ('worker-'+[guid]::NewGuid().ToString('N')+'.out')
    $Context.errorPath=$Context.outputPath+'.err'
    $Context.process=Start-Process powershell.exe -ArgumentList @('-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-OutputFormat','Text','-EncodedCommand',$encoded) -WindowStyle Hidden -RedirectStandardOutput $Context.outputPath -RedirectStandardError $Context.errorPath -PassThru
    $Context.busy=$true;$Context.lastProgress='';$Context.action.Enabled=$false;$Context.close.Enabled=$false
    foreach($control in $Context.inputs){$control.Enabled=$false}
    $Context.status.ForeColor=[Drawing.ColorTranslator]::FromHtml('#656d65');$Context.status.Text='正在准备升级'
    $Context.timer.Start()
}

function New-AgentUpgradeForm([string]$SourceRoot,[string]$InstallDir,[string]$DataDir){
    Add-Type -AssemblyName System.Windows.Forms,System.Drawing
    [Windows.Forms.Application]::EnableVisualStyles()
    $form=[Windows.Forms.Form]::new();$form.Text='SpiderFly · 升级 Agent'
    $form.ClientSize=[Drawing.Size]::new(680,420);$form.MinimumSize=[Drawing.Size]::new(620,450)
    $form.StartPosition='CenterScreen';$form.BackColor=[Drawing.Color]::White
    $form.Font=[Drawing.Font]::new('Microsoft YaHei UI',9)
    $form.MaximizeBox=$false;$form.FormBorderStyle='FixedDialog'
    $title=[Windows.Forms.Label]::new();$title.Text='升级 Agent';$title.Font=[Drawing.Font]::new('Microsoft YaHei UI',16,[Drawing.FontStyle]::Bold);$title.SetBounds(24,20,610,36)
    $hint=[Windows.Forms.Label]::new();$hint.Text='任务和回传结束后，一键备份、升级并重启。';$hint.ForeColor=[Drawing.ColorTranslator]::FromHtml('#656d65');$hint.SetBounds(26,62,620,25)
    $installLabel=[Windows.Forms.Label]::new();$installLabel.Text='原 Agent 安装目录';$installLabel.SetBounds(24,104,400,24)
    $install=[Windows.Forms.TextBox]::new();$install.SetBounds(24,132,516,28);$install.Text=$InstallDir
    $browse=[Windows.Forms.Button]::new();$browse.Text='选择目录';$browse.SetBounds(552,130,104,32)
    $packageLabel=[Windows.Forms.Label]::new();$packageLabel.Text='升级包';$packageLabel.SetBounds(24,182,400,24)
    $package=[Windows.Forms.TextBox]::new();$package.SetBounds(24,210,516,28)
    if(-not(Test-Path -LiteralPath (Join-Path $SourceRoot '.venv')) -and (Test-Path -LiteralPath (Join-Path $SourceRoot 'update-manifest.json'))){$package.Text=$SourceRoot}
    $pick=[Windows.Forms.Button]::new();$pick.Text='选择 ZIP';$pick.SetBounds(552,208,104,32)
    $advanced=[Windows.Forms.LinkLabel]::new();$advanced.Text='更多设置';$advanced.LinkColor=[Drawing.ColorTranslator]::FromHtml('#009139');$advanced.SetBounds(24,256,150,24)
    $dataLabel=[Windows.Forms.Label]::new();$dataLabel.Text='Agent 数据目录';$dataLabel.SetBounds(24,286,400,24);$dataLabel.Visible=$false
    $data=[Windows.Forms.TextBox]::new();$data.Text=$DataDir;$data.SetBounds(24,314,632,28);$data.Visible=$false
    $status=[Windows.Forms.Label]::new();$status.Text='原机器身份和运行记录会保留。';$status.ForeColor=[Drawing.ColorTranslator]::FromHtml('#656d65');$status.SetBounds(24,296,632,48)
    $action=[Windows.Forms.Button]::new();$action.Text='一键升级';$action.SetBounds(532,366,124,36)
    $action.BackColor=[Drawing.ColorTranslator]::FromHtml('#009139');$action.ForeColor=[Drawing.Color]::White;$action.FlatStyle='Flat';$action.FlatAppearance.BorderSize=0
    $close=[Windows.Forms.Button]::new();$close.Text='关闭';$close.SetBounds(416,366,104,36)
    $logs=[Windows.Forms.LinkLabel]::new();$logs.Text='查看日志';$logs.SetBounds(24,372,150,24);$logs.Visible=$false
    foreach($button in @($browse,$pick,$close)){$button.FlatStyle='Flat';$button.BackColor=[Drawing.Color]::White;$button.FlatAppearance.BorderColor=[Drawing.ColorTranslator]::FromHtml('#dfe3df')}
    $timer=[Windows.Forms.Timer]::new();$timer.Interval=200
    $ctx=[pscustomobject]@{form=$form;sourceRoot=$SourceRoot;install=$install;package=$package;data=$data;status=$status;action=$action;close=$close;logs=$logs;timer=$timer;inputs=@($install,$browse,$package,$pick,$advanced,$data);job='';process=$null;busy=$false;resumePending=$false;lastProgress='';outputPath='';errorPath=''}
    $form.Tag=$ctx
    $browse.Add_Click({
        $dialog=[Windows.Forms.FolderBrowserDialog]::new();$dialog.Description='选择原 Agent 安装目录'
        if($dialog.ShowDialog($form) -eq 'OK'){$install.Text=$dialog.SelectedPath};$dialog.Dispose()
    }.GetNewClosure())
    $pick.Add_Click({
        $dialog=[Windows.Forms.OpenFileDialog]::new();$dialog.Filter='Agent 升级包 (*.zip)|*.zip';$dialog.Title='选择主控下载的 Agent 包'
        if($dialog.ShowDialog($form) -eq 'OK'){$package.Text=$dialog.FileName};$dialog.Dispose()
    }.GetNewClosure())
    $advanced.Add_Click({
        $data.Visible=-not $data.Visible;$dataLabel.Visible=$data.Visible
        if($data.Visible){$form.ClientSize=[Drawing.Size]::new(680,492);$status.Top=356;$action.Top=438;$close.Top=438;$logs.Top=444}
        else{$form.ClientSize=[Drawing.Size]::new(680,420);$status.Top=296;$action.Top=366;$close.Top=366;$logs.Top=372}
    }.GetNewClosure())
    $action.Add_Click({
        try{Start-AgentUpgradeProcess $ctx}catch{$status.ForeColor=[Drawing.ColorTranslator]::FromHtml('#a53721');$status.Text=$_.Exception.Message}
    }.GetNewClosure())
    $close.Add_Click({$form.Close()}.GetNewClosure())
    $logs.Add_Click({if($ctx.job){Start-Process -FilePath notepad.exe -ArgumentList ('"'+(Join-Path $ctx.job 'upgrade.log')+'"')}}.GetNewClosure())
    $timer.Add_Tick({
        $path=Join-Path $ctx.job 'progress.json'
        try{
            if(Test-Path -LiteralPath $path){
                $progress=Get-Content -LiteralPath $path -Raw -Encoding UTF8|ConvertFrom-Json
                $ctx.status.Text=$progress.message
                $ctx.lastProgress=$progress
            }
        }catch{}
        if($ctx.process -and $ctx.process.HasExited){
            $ctx.timer.Stop();$ctx.busy=$false;$ctx.close.Enabled=$true;$ctx.logs.Visible=$true
            # The child can finish between the first read and HasExited.
            try{$ctx.lastProgress=Get-Content -LiteralPath $path -Raw -Encoding UTF8|ConvertFrom-Json;$ctx.status.Text=$ctx.lastProgress.message}catch{}
            $out=[IO.File]::ReadAllText($ctx.outputPath);$err=[IO.File]::ReadAllText($ctx.errorPath)
            if($out -or $err){[IO.File]::AppendAllText((Join-Path $ctx.job 'upgrade.log'),$out+"`r`n"+$err,[Text.UTF8Encoding]::new($false))}
            if(-not $ctx.lastProgress -or -not $ctx.lastProgress.done){$ctx.status.Text='升级未完成，请查看日志';$ctx.status.ForeColor=[Drawing.ColorTranslator]::FromHtml('#a53721')}
            else{$ctx.status.ForeColor=[Drawing.ColorTranslator]::FromHtml($(if($ctx.lastProgress.success){'#009139'}else{'#a53721'}))}
            $ctx.resumePending=[bool]$ctx.lastProgress.resume_pending
            if($ctx.resumePending){$ctx.action.Text='恢复派发';$ctx.action.Enabled=$true}
            elseif($ctx.lastProgress.success){$ctx.action.Text='已完成';$ctx.action.Enabled=$false}
            else{$ctx.action.Text='重试';$ctx.action.Enabled=$true}
            foreach($control in $ctx.inputs){$control.Enabled=-not $ctx.resumePending}
            $ctx.process.Dispose();$ctx.process=$null
        }
    }.GetNewClosure())
    $form.Add_FormClosing({param($sender,$eventArgs) if($ctx.busy){$eventArgs.Cancel=$true;$ctx.status.Text='升级中，请等待完成'}else{$ctx.timer.Dispose()}}.GetNewClosure())
    $form.Controls.AddRange(@($title,$hint,$installLabel,$install,$browse,$packageLabel,$package,$pick,$advanced,$dataLabel,$data,$status,$logs,$action,$close))
    $form.AcceptButton=$action;$form.CancelButton=$close
    return $form
}
