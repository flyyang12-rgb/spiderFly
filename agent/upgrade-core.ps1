# Reuses manage.ps1 for stopping, state backup, program replacement and startup.
$ErrorActionPreference='Stop'

function Quote-UpgradeLiteral([string]$Value){ return "'"+$Value.Replace("'","''")+"'" }

function Test-UpgradeProgramPath([string]$Name){
    return $Name -match '^spiderfly_agent/[A-Za-z_][A-Za-z0-9_]*\.py$' -or $Name -in @(
        'README.md','requirements.txt','start.ps1','manage.ps1','setup.ps1','install-python.ps1',
        '安装并接入Agent.bat','upgrade.ps1','upgrade-core.ps1','upgrade-ui.ps1','upgrade-transport.py','升级Agent.bat','remote-upgrade.ps1','controller-trust.json')
}

function Get-UpgradeVersion([string]$Directory){
    $file=Join-Path $Directory 'spiderfly_agent/__init__.py'
    $match=[regex]::Match([IO.File]::ReadAllText($file),'(?m)^__version__\s*=\s*["''](\d+\.\d+\.\d+)["'']')
    if(-not $match.Success){throw '无法读取 Agent 版本'}
    return $match.Groups[1].Value
}

function Write-UpgradeJson([string]$Path,$Value){
    $temporary=$Path+'.tmp'
    [IO.File]::WriteAllText($temporary,($Value|ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))
    if(Test-Path -LiteralPath $Path){
        $previous=$Path+'.previous'
        [IO.File]::Replace($temporary,$Path,$previous)
        Remove-Item -LiteralPath $previous -Force
    }
    else{[IO.File]::Move($temporary,$Path)}
}

function Set-UpgradeProgress([string]$Job,[string]$Message,[bool]$Done=$false,[bool]$Success=$false,[bool]$ResumePending=$false){
    Write-UpgradeJson (Join-Path $Job 'progress.json') @{message=$Message;done=$Done;success=$Success;resume_pending=$ResumePending}
}

function Expand-UpgradeArchive([string]$Zip,[string]$Destination,[hashtable]$OnlyFiles=$null){
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    if((Get-Item -LiteralPath $Zip).Length -gt 16MB){throw '升级包超过 16 MiB'}
    $destinationRoot=[IO.Path]::GetFullPath($Destination).TrimEnd('\')
    New-Item -ItemType Directory -Force -Path $destinationRoot|Out-Null
    $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $archive=[IO.Compression.ZipFile]::OpenRead($Zip)
    $total=0
    try{
        if($archive.Entries.Count -gt 1000){throw '升级包文件过多'}
        foreach($entry in $archive.Entries){
            $name=$entry.FullName.Replace('\','/')
            if(-not $entry.Name){continue}
            if($OnlyFiles){
                if(-not $OnlyFiles.ContainsKey($name)){continue}
            }else{
                if(-not $name.StartsWith('SpiderFlyAgent/')){throw '请选择从主控下载的 Agent 升级包'}
                $name=$name.Substring(15)
                if($name -ne 'update-manifest.json' -and -not(Test-UpgradeProgramPath $name)){throw '升级包含非程序文件'}
            }
            if(-not $seen.Add($name)){throw '升级包文件名重复'}
            if($entry.Length -gt 2MB -or (($entry.ExternalAttributes -shr 16) -band 61440) -eq 40960){throw '升级包含超限文件或链接'}
            $target=[IO.Path]::GetFullPath((Join-Path $destinationRoot $name))
            if(-not $target.StartsWith($destinationRoot+'\',[StringComparison]::OrdinalIgnoreCase)){throw '升级包路径越界'}
            New-Item -ItemType Directory -Force -Path (Split-Path $target -Parent)|Out-Null
            $inputStream=$entry.Open();$outputStream=[IO.File]::Open($target,[IO.FileMode]::CreateNew)
            try{
                $buffer=New-Object byte[] 8192;$length=0
                while(($read=$inputStream.Read($buffer,0,$buffer.Length)) -gt 0){
                    $length+=$read;$total+=$read
                    if($length -gt 2MB -or $total -gt 8MB){throw '升级包解压大小超过限制'}
                    $outputStream.Write($buffer,0,$read)
                }
            }finally{$inputStream.Dispose();$outputStream.Dispose()}
        }
    }finally{$archive.Dispose()}
}

function Copy-VerifiedUpgradePackage([string]$Package,[string]$Job){
    $source=[IO.Path]::GetFullPath($Package)
    if(Test-Path -LiteralPath $source -PathType Leaf){
        $source=Join-Path $Job 'unpacked';Expand-UpgradeArchive $Package $source
    }
    $manifestPath=Join-Path $source 'update-manifest.json'
    if(-not(Test-Path -LiteralPath $manifestPath) -or (Get-Item -LiteralPath $manifestPath).Length -gt 256KB){throw '升级包缺少校验清单，请重新从主控下载'}
    $manifest=Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8|ConvertFrom-Json
    if($manifest.format -ne 1 -or $manifest.version -notmatch '^\d+\.\d+\.\d+$' -or -not $manifest.files){throw '升级包校验清单无效'}
    $names=@($manifest.files.PSObject.Properties.Name)
    if($names.Count -lt 17 -or $names.Count -gt 100){throw '升级包文件清单无效'}
    $staged=Join-Path $Job 'package';New-Item -ItemType Directory -Path $staged|Out-Null
    $total=0
    foreach($name in $names){
        if(-not(Test-UpgradeProgramPath $name) -or $manifest.files.$name -notmatch '^[a-f0-9]{64}$'){throw '升级包清单含无效路径或哈希'}
        $file=[IO.Path]::GetFullPath((Join-Path $source $name))
        if(-not $file.StartsWith($source.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase) -or -not(Test-Path -LiteralPath $file -PathType Leaf)){throw '升级包文件不完整'}
        $info=Get-Item -LiteralPath $file
        $parent=Get-Item -LiteralPath (Split-Path $file -Parent)
        if(($info.Attributes -band [IO.FileAttributes]::ReparsePoint) -or ($parent.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $info.Length -gt 2MB){throw '升级包文件不能是链接或超限文件'}
        $total+=$info.Length;if($total -gt 8MB){throw '升级包超过解压限制'}
        $copy=Join-Path $staged $name;New-Item -ItemType Directory -Force -Path (Split-Path $copy -Parent)|Out-Null
        Copy-Item -LiteralPath $file -Destination $copy
        if((Get-FileHash -LiteralPath $copy -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.files.$name){throw '升级包校验失败，请重新下载'}
    }
    foreach($required in @('manage.ps1','start.ps1','requirements.txt','upgrade-core.ps1','upgrade-ui.ps1','upgrade.ps1','upgrade-transport.py','升级Agent.bat','spiderfly_agent/__init__.py','spiderfly_agent/api.py','spiderfly_agent/identity.py','spiderfly_agent/client.py','spiderfly_agent/worker.py','spiderfly_agent/environment.py','spiderfly_agent/desktop.py')){
        if($required -notin $names){throw '升级包缺少必要程序'}
    }
    if((Get-UpgradeVersion $staged) -ne $manifest.version){throw '升级包版本与清单不一致'}
    if([version]$manifest.version -ge [version]'0.5.0'){
        foreach($required in @('remote-upgrade.ps1','controller-trust.json','spiderfly_agent/updater.py')){
            if($required -notin $names){throw '升级包缺少远程升级程序'}
        }
    }
    Copy-Item -LiteralPath $manifestPath -Destination (Join-Path $staged 'update-manifest.json')
    return @{root=$staged;version=$manifest.version;files=$names}
}

function Invoke-UpgradeTransport([string]$Python,[string]$Helper,[string]$Data,[string]$Action,[string]$Token){
    $arguments=@('-X','utf8','-I',$Helper,$Action,'--data-dir',$Data)
    if($Token){$arguments+=@('--token',$Token)}
    $output=& $Python @arguments 2>&1
    if($LASTEXITCODE -ne 0){throw (($output|ForEach-Object{[string]$_}) -join "`n")}
    return (($output -join "`n")|ConvertFrom-Json)
}

function Invoke-UpgradeManage([string]$Manage,[string]$Install,[string]$Data,[string]$Action,[string]$Job,[string[]]$Extra=@()){
    $command='[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); $ProgressPreference=''SilentlyContinue''; & '+(Quote-UpgradeLiteral $Manage)+' '+$Action+' -InstallDir '+(Quote-UpgradeLiteral $Install)+' -DataDir '+(Quote-UpgradeLiteral $Data)
    foreach($value in $Extra){
        if($value -in @('-Dispatch','-BackupPath','-PackagePath','-ProgramBackupPath')){$command+=' '+$value}
        else{$command+=' '+(Quote-UpgradeLiteral $value)}
    }
    $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    # File handles let the manager exit while its Agent keeps running. Captured
    # pipes may stay open in descendants on Windows PowerShell/.NET Framework.
    $outPath=Join-Path $Job ('manage-'+$Action+'-'+[guid]::NewGuid().ToString('N')+'.out')
    $errPath=$outPath+'.err'
    $process=Start-Process powershell.exe -ArgumentList @('-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-OutputFormat','Text','-EncodedCommand',$encoded) -WindowStyle Hidden -RedirectStandardOutput $outPath -RedirectStandardError $errPath -PassThru
    try{$null=$process.Handle;$process.WaitForExit();$code=$process.ExitCode}finally{$process.Dispose()}
    $output=@(Get-Content -LiteralPath $outPath -Encoding UTF8)+@(Get-Content -LiteralPath $errPath -Encoding UTF8)
    $output|Out-File -LiteralPath (Join-Path $Job 'upgrade.log') -Append -Encoding utf8
    if($code -ne 0){throw ('操作未完成：'+$Action+'。'+(($output|ForEach-Object{[string]$_}) -join ' '))}
}

function Get-UpgradeProgramHashes([string]$Install){
    $result=@{}
    $module=Join-Path $Install 'spiderfly_agent'
    if((Get-Item -LiteralPath $module).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Agent 程序目录含链接'}
    $files=@(Get-ChildItem -LiteralPath $Install -File)+@(Get-ChildItem -LiteralPath $module -File)
    foreach($file in $files){
        $name=$file.FullName.Substring($Install.TrimEnd('\').Length+1).Replace('\','/')
        if((Test-UpgradeProgramPath $name) -or $name -eq 'update-manifest.json'){
            if($file.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Agent 程序目录含链接'}
            $result[$name]=(Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
        }
    }
    return $result
}

function Clear-UpgradeBytecode([string]$Install){
    $root=[IO.Path]::GetFullPath($Install).TrimEnd('\')
    $cache=Join-Path $root 'spiderfly_agent/__pycache__'
    if(-not(Test-Path -LiteralPath $cache)){return}
    if((Get-Item -LiteralPath $cache).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Agent 缓存目录含链接'}
    foreach($file in Get-ChildItem -LiteralPath $cache -File -Filter '*.pyc'){
        if(-not $file.FullName.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Agent 缓存路径越界'}
        Remove-Item -LiteralPath $file.FullName -Force
    }
}

function Restore-UpgradeProgram([string]$Install,[string]$Archive,[hashtable]$Before,[string[]]$NewFiles,[string]$Job){
    $restore=Join-Path $Job 'restore-program';Expand-UpgradeArchive $Archive $restore $Before
    foreach($name in $Before.Keys){
        $file=Join-Path $restore $name
        if(-not(Test-Path -LiteralPath $file) -or (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $Before[$name]){throw '旧程序备份校验失败，保留现场供恢复'}
    }
    foreach($name in $Before.Keys){
        $destination=Join-Path $Install $name
        New-Item -ItemType Directory -Force -Path (Split-Path $destination -Parent)|Out-Null
        Copy-Item -LiteralPath (Join-Path $restore $name) -Destination $destination -Force
    }
    foreach($name in @($NewFiles)+@('update-manifest.json')){
        if($Before.ContainsKey($name)){continue}
        $destination=[IO.Path]::GetFullPath((Join-Path $Install $name))
        if(-not $destination.StartsWith($Install.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase) -or
           (-not(Test-UpgradeProgramPath $name) -and $name -ne 'update-manifest.json')){throw '回退文件路径越界'}
        if(Test-Path -LiteralPath $destination -PathType Leaf){Remove-Item -LiteralPath $destination}
    }
}

function Invoke-AgentUpgrade([string]$InstallDir,[string]$DataDir,[string]$PackagePath,[string]$JobDir,[switch]$ResumeOnly,[string]$PauseToken,[switch]$DeferResume){
    $install=[IO.Path]::GetFullPath($InstallDir).TrimEnd('\');$data=[IO.Path]::GetFullPath($DataDir).TrimEnd('\')
    $job=[IO.Path]::GetFullPath($JobDir);New-Item -ItemType Directory -Force -Path $job|Out-Null
    $python=Join-Path $install '.venv/Scripts/python.exe'
    $pause=$false;$stopped=$false;$changed=$false;$restored=$false;$pending=$false;$success=$false;$message=''
    $token=[guid]::NewGuid().ToString('N');$package=$null;$before=$null;$dispatch=$false
    if($PauseToken){if($PauseToken -notmatch '^[a-f0-9]{32}$'){throw '升级操作标识无效'};$token=$PauseToken}
    $hash=[Security.Cryptography.SHA256]::Create()
    try{$lockName=[BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($data.ToLowerInvariant()))).Replace('-','')}finally{$hash.Dispose()}
    $mutex=[Threading.Mutex]::new($false,'Global\SpiderFly.AgentUpgrade.'+$lockName);$owned=$false
    try{
        try{$owned=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$owned=$true}
        if(-not $owned){throw '这台 Agent 已有升级操作正在进行'}
        if($ResumeOnly){
            $operation=Get-Content -LiteralPath (Join-Path $job 'operation.json') -Raw -Encoding UTF8|ConvertFrom-Json
            $null=Invoke-UpgradeTransport $python $operation.helper $data 'resume' $operation.token
            Remove-Item -LiteralPath (Join-Path $data 'upgrade.resume.json') -ErrorAction SilentlyContinue
            Set-UpgradeProgress $job '派发已恢复' $true $true
            return
        }
        Set-UpgradeProgress $job '正在校验升级包'
        if(-not(Test-Path -LiteralPath $python) -or -not(Test-Path -LiteralPath (Join-Path $install 'manage.ps1'))){throw '请选择原 Agent 安装目录'}
        if((Get-Item -LiteralPath $install).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Agent 安装目录不能是链接'}
        $oldVersion=Get-UpgradeVersion $install
        $package=Copy-VerifiedUpgradePackage $PackagePath $job
        if([version]$package.version -le [version]$oldVersion){throw '所选包不是更新版本'}
        $manage=Join-Path $package.root 'manage.ps1';$helper=Join-Path $package.root 'upgrade-transport.py'
        $state=Invoke-UpgradeTransport $python $helper $data 'inspect' ''
        if($state.unacknowledged -ne 0){throw '任务或回传尚未结束，请稍后再升级'}
        Set-UpgradeProgress $job '正在暂停新派发'
        Write-UpgradeJson (Join-Path $job 'operation.json') @{token=$token;helper=$helper;install=$install;data=$data;old_version=$oldVersion;new_version=$package.version}
        # A lost pause response may already have committed on the controller.
        # Always attempt an idempotent resume, even when that response is lost.
        $pause=$true
        $paused=Invoke-UpgradeTransport $python $helper $data 'pause' $token
        $dispatch=[bool]$paused.dispatch_enabled
        $state=Invoke-UpgradeTransport $python $helper $data 'inspect' ''
        if($state.unacknowledged -ne 0){throw '检测到新的领取或回传，未停止 Agent，请稍后再试'}
        Set-UpgradeProgress $job '正在安全停止 Agent'
        Invoke-UpgradeManage $manage $install $data 'Stop' $job
        $stopped=$true
        $state=Invoke-UpgradeTransport $python $helper $data 'inspect' ''
        if($state.unacknowledged -ne 0){throw '仍有未回传结果，未替换程序'}
        Set-UpgradeProgress $job '正在备份'
        Invoke-UpgradeManage $manage $install $data 'Backup' $job @('-BackupPath',(Join-Path $job 'state-before.zip'))
        $before=Get-UpgradeProgramHashes $install
        Write-UpgradeJson (Join-Path $job 'program-hashes.json') $before
        Set-UpgradeProgress $job '正在升级程序'
        $changed=$true
        Invoke-UpgradeManage $manage $install $data 'Upgrade' $job @('-PackagePath',$package.root,'-ProgramBackupPath',(Join-Path $job 'program-before.zip'))
        Clear-UpgradeBytecode $install
        Set-UpgradeProgress $job '正在启动并校验'
        if(-not $DeferResume){Write-UpgradeJson (Join-Path $data 'upgrade.resume.json') @{token=$token;version=$package.version}}
        $startArgs=@();if($dispatch){$startArgs=@('-Dispatch')}
        Invoke-UpgradeManage $manage $install $data 'Start' $job $startArgs
        $record=Get-Content -LiteralPath (Join-Path $data 'agent.pid.json') -Raw -Encoding UTF8|ConvertFrom-Json
        if($record.version -ne $package.version){throw '启动后的 Agent 版本与升级包不一致'}
        $success=$true;$message='已升级至 '+$package.version
    }catch{
        if($ResumeOnly){$pending=$true}
        $message=$_.Exception.Message
        $message|Out-File -LiteralPath (Join-Path $job 'upgrade.log') -Append -Encoding utf8
        if($changed -and $before -and (Test-Path -LiteralPath (Join-Path $job 'program-before.zip'))){
            try{
                Set-UpgradeProgress $job '升级未完成，正在恢复旧程序'
                Invoke-UpgradeManage $manage $install $data 'Stop' $job
                Restore-UpgradeProgram $install (Join-Path $job 'program-before.zip') $before $package.files $job
                Clear-UpgradeBytecode $install
                $restored=$true
            }catch{$message+='；旧程序恢复未完成：'+$_.Exception.Message}
        }
        if($changed -and $before -and -not(Test-Path -LiteralPath (Join-Path $job 'program-before.zip'))){
            $unchanged=$true
            foreach($name in $before.Keys){
                $file=Join-Path $install $name
                if(-not(Test-Path -LiteralPath $file) -or (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $before[$name]){$unchanged=$false;break}
            }
            if($unchanged){$changed=$false}
        }
        if($stopped -and (-not $changed -or $restored)){
            try{
                if(-not $DeferResume){Write-UpgradeJson (Join-Path $data 'upgrade.resume.json') @{token=$token;version=$oldVersion}}
                $startArgs=@();if($dispatch){$startArgs=@('-Dispatch')}
                Invoke-UpgradeManage $manage $install $data 'Start' $job $startArgs
                $message+='；旧版已恢复运行'
            }catch{$message+='；旧版启动未完成：'+$_.Exception.Message}
        }
    }finally{
        if($pause -and -not $DeferResume){
            try{
                $null=Invoke-UpgradeTransport $python $helper $data 'resume' $token
                Remove-Item -LiteralPath (Join-Path $data 'upgrade.resume.json') -ErrorAction SilentlyContinue
            }catch{$pending=$true;$message+='；等待连接主控恢复派发'}
        }
        if($owned){$mutex.ReleaseMutex()};$mutex.Dispose()
    }
    Set-UpgradeProgress $job $message $true $success $pending
}
