param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$repoPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $repoPath

$resolvedPython = Get-Command $Python -ErrorAction SilentlyContinue
if (-not $resolvedPython -or $resolvedPython.Source -like '*Microsoft\WindowsApps*') {
    $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundledPython) { $Python = $bundledPython }
    else { throw '需要 Python 3.12+。安装后重试，或使用 -Python 指定 python.exe 的绝对路径。' }
}
& $Python -c "import sys; assert sys.version_info >= (3, 12), 'Python 3.12+ is required'"
if ($LASTEXITCODE -ne 0) { throw '需要 Python 3.12 或更高版本' }
if (-not (Test-Path -LiteralPath 'backend/.venv/Scripts/python.exe')) {
    & $Python -m venv backend/.venv
    if ($LASTEXITCODE -ne 0) { throw '创建 Python 虚拟环境失败' }
}
& backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.lock
if ($LASTEXITCODE -ne 0) { throw '安装后端依赖失败' }
& backend/.venv/Scripts/python.exe -m pip install --no-deps -e backend
if ($LASTEXITCODE -ne 0) { throw '安装本地后端失败' }
Push-Location frontend
try { npm ci; if ($LASTEXITCODE -ne 0) { throw '安装前端依赖失败' } }
finally { Pop-Location }
& backend/.venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) { throw '数据库迁移失败' }
Write-Output '配置完成。运行 .\scripts\dev.ps1 启动本地应用。默认演示模式不需要 API Key。'
