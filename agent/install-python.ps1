param(
    [string]$LogPath = (Join-Path $env:TEMP 'SpiderFlyAgent-python-install.log')
)

$ErrorActionPreference = 'Stop'
$installerUrl = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe'
$downloadDirectory = Join-Path $env:TEMP ('SpiderFlyAgent-Python-' + [guid]::NewGuid().ToString('N'))
$installerPath = Join-Path $downloadDirectory 'python-3.12.10-amd64.exe'

function Write-InstallLog([string]$Message) {
    Add-Content -LiteralPath $LogPath -Value $Message -Encoding UTF8
}

function Test-Python312 {
    $candidates = @()
    if ($env:LOCALAPPDATA) { $candidates += Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe' }
    if ($env:ProgramFiles) { $candidates += Join-Path $env:ProgramFiles 'Python312\python.exe' }
    $candidates += 'python'
    foreach ($candidate in $candidates) {
        try {
            & $candidate -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3,12) and sys.maxsize > 2**32 else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { return $true }
        } catch { }
    }
    return $false
}

try {
    if (Test-Python312) {
        Write-InstallLog '已检测到 64 位 Python 3.12，无需重复安装。'
        exit 0
    }
    if (-not [Environment]::Is64BitOperatingSystem) { throw '这台电脑不是 64 位 Windows，无法安装 64 位 Python 3.12。' }

    New-Item -ItemType Directory -Path $downloadDirectory -Force | Out-Null
    Write-InstallLog '正在从 python.org 下载 Python 3.12.10 安装程序。'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri $installerUrl -OutFile $installerPath
    $signature = Get-AuthenticodeSignature -LiteralPath $installerPath
    if ($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate -or
            $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
        throw 'Python 安装程序的数字签名未通过验证，已停止安装。'
    }

    Write-InstallLog '下载完成，正在为当前 Windows 用户安装 Python 并配置 PATH。'
    $arguments = @('/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_pip=1', 'Include_test=0')
    $process = Start-Process -FilePath $installerPath -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "Python 安装程序退出码：$($process.ExitCode)。" }
    if (-not (Test-Python312)) { throw '安装程序已结束，但未检测到 64 位 Python 3.12。' }
    Write-InstallLog 'Python 3.12 安装完成，已设置当前用户 PATH。'
    exit 0
} catch {
    Write-InstallLog ('自动安装失败：' + $_.Exception.Message)
    exit 1
} finally {
    if (Test-Path -LiteralPath $downloadDirectory) {
        Remove-Item -LiteralPath $downloadDirectory -Recurse -Force -ErrorAction SilentlyContinue
    }
}
