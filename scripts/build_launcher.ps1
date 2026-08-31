[CmdletBinding()]
param(
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$sourcePath = Join-Path $projectRoot 'launcher\SpiderFlyLauncher.cs'

if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
    throw "没有找到启动器源码：$sourcePath"
}

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $projectRoot 'SpiderFly.exe'
} elseif (-not [System.IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path $projectRoot $OutputPath
}
$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)
if ((Split-Path -Parent $OutputPath) -ne $projectRoot) {
    Write-Warning '该 EXE 不在 SpiderFly 项目根目录，只适合编译验证；实际运行时请使用默认输出位置。'
}

$compilerCandidates = @(
    (Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'),
    (Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe')
)
$compiler = $compilerCandidates |
    Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
    Select-Object -First 1

if (-not $compiler) {
    $command = Get-Command csc.exe -ErrorAction SilentlyContinue
    if ($command) {
        $compiler = $command.Source
    }
}
if (-not $compiler) {
    throw '本机没有找到可用的 C# 编译器，未生成 SpiderFly.exe。'
}

$outputDirectory = Split-Path -Parent $OutputPath
if ($outputDirectory) {
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
}

$compilerArguments = @(
    '/nologo',
    '/target:winexe',
    '/platform:anycpu',
    '/optimize+',
    '/codepage:65001',
    '/reference:System.Windows.Forms.dll',
    "/out:$OutputPath",
    $sourcePath
)

Write-Host '[SpiderFly] 正在编译轻量 Windows 启动器……' -ForegroundColor Cyan
& $compiler @compilerArguments
if ($LASTEXITCODE -ne 0) {
    throw "启动器编译失败，C# 编译器退出码：$LASTEXITCODE"
}
if (-not (Test-Path -LiteralPath $OutputPath -PathType Leaf)) {
    throw "编译器没有生成预期文件：$OutputPath"
}

$item = Get-Item -LiteralPath $OutputPath
Write-Host '[SpiderFly] 启动器已经生成：' -ForegroundColor Green
Write-Host ("  {0} ({1:N0} 字节)" -f $item.FullName, $item.Length) -ForegroundColor Green
