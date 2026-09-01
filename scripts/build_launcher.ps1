[CmdletBinding()]
param(
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$sourcePath = Join-Path $projectRoot 'launcher\SpiderFlyLauncher.cs'

if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
    throw "Launcher source was not found: $sourcePath"
}

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $projectRoot 'SpiderFly.exe'
} elseif (-not [System.IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path $projectRoot $OutputPath
}
$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)
if ((Split-Path -Parent $OutputPath) -ne $projectRoot) {
    Write-Warning 'This EXE is outside the SpiderFly project root and is only suitable for build verification. Use the default output path for normal operation.'
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
    throw 'No compatible C# compiler was found. SpiderFly.exe was not generated.'
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

Write-Host '[SpiderFly] Building the lightweight Windows launcher...' -ForegroundColor Cyan
& $compiler @compilerArguments
if ($LASTEXITCODE -ne 0) {
    throw "Launcher compilation failed. C# compiler exit code: $LASTEXITCODE"
}
if (-not (Test-Path -LiteralPath $OutputPath -PathType Leaf)) {
    throw "The compiler did not create the expected file: $OutputPath"
}

$item = Get-Item -LiteralPath $OutputPath
Write-Host '[SpiderFly] Launcher generated:' -ForegroundColor Green
Write-Host ("  {0} ({1:N0} bytes)" -f $item.FullName, $item.Length) -ForegroundColor Green
