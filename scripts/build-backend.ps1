$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$BundleRoot = Join-Path $ProjectRoot 'build\backend-dist'
$BinariesRoot = Join-Path $ProjectRoot 'frontend\src-tauri\binaries'

& $Python -m PyInstaller `
  --noconfirm `
  --clean `
  --onedir `
  --contents-directory backend-runtime `
  --name yus-ai-backend-x86_64-pc-windows-msvc `
  --distpath $BundleRoot `
  --workpath (Join-Path $ProjectRoot 'build\pyinstaller') `
  --specpath (Join-Path $ProjectRoot 'build') `
  (Join-Path $ProjectRoot 'backend\run_server.py')
if ($LASTEXITCODE -ne 0) { throw 'Backend packaging failed.' }
$BackendBundle = Join-Path $BundleRoot 'yus-ai-backend-x86_64-pc-windows-msvc'
$RuntimeDestination = Join-Path $BinariesRoot 'backend-runtime'
# Only replace generated runtime files inside the verified project binaries directory.
if (Test-Path -LiteralPath $RuntimeDestination) {
  $ResolvedRuntime = (Resolve-Path -LiteralPath $RuntimeDestination).Path
  if ($ResolvedRuntime -ne "$BinariesRoot\backend-runtime") { throw 'Unexpected runtime directory.' }
  Remove-Item -LiteralPath $ResolvedRuntime -Recurse -Force
}
Copy-Item -LiteralPath (Join-Path $BackendBundle 'backend-runtime') -Destination $RuntimeDestination -Recurse
Copy-Item -LiteralPath (Join-Path $BackendBundle 'yus-ai-backend-x86_64-pc-windows-msvc.exe') -Destination $BinariesRoot -Force
