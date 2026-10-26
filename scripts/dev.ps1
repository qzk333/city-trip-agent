$ErrorActionPreference = 'Stop'
$repoPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $repoPath 'backend/.venv/Scripts/python.exe'
$nodePath = (Get-Command node -ErrorAction Stop).Source
$vitePath = Join-Path $repoPath 'frontend/node_modules/vite/bin/vite.js'
if (-not (Test-Path -LiteralPath $pythonPath) -or -not (Test-Path -LiteralPath $vitePath)) {
    throw '请先运行 .\scripts\setup.ps1'
}
foreach ($portNumber in @(8000, 5173)) {
    if (Get-NetTCPConnection -State Listen -LocalPort $portNumber -ErrorAction SilentlyContinue) {
        throw "端口 $portNumber 已被使用。请关闭已有开发服务后重试。"
    }
}
$logsPath = Join-Path $repoPath 'logs'
New-Item -ItemType Directory -Path $logsPath -Force | Out-Null
$backendProcess = $null
$frontendProcess = $null
try {
    $backendProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000') -WorkingDirectory (Join-Path $repoPath 'backend') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logsPath 'backend.log') -RedirectStandardError (Join-Path $logsPath 'backend.err.log')
    $frontendProcess = Start-Process -FilePath $nodePath -ArgumentList @(('"{0}"' -f $vitePath), '--host', '127.0.0.1', '--port', '5173', '--strictPort') -WorkingDirectory (Join-Path $repoPath 'frontend') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logsPath 'frontend.log') -RedirectStandardError (Join-Path $logsPath 'frontend.err.log')
    Write-Output '页面：http://127.0.0.1:5173'
    Write-Output '接口文档：http://127.0.0.1:8000/docs'
    Write-Output '日志位于 logs。按 Ctrl+C 结束本次启动的两个服务。'
    while (-not $backendProcess.HasExited -and -not $frontendProcess.HasExited) { Start-Sleep -Seconds 1 }
    throw '开发服务已退出，请检查 logs 下的错误日志。'
} finally {
    foreach ($childProcess in @($backendProcess, $frontendProcess)) {
        if ($null -ne $childProcess -and -not $childProcess.HasExited) { $childProcess.Kill() }
    }
}
