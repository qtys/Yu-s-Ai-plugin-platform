$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

& $Python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --name yus-ai-backend-x86_64-pc-windows-msvc `
  --distpath (Join-Path $ProjectRoot 'frontend\src-tauri\binaries') `
  --workpath (Join-Path $ProjectRoot 'build\pyinstaller') `
  --specpath (Join-Path $ProjectRoot 'build') `
  (Join-Path $ProjectRoot 'backend\run_server.py')
