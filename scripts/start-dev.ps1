param([switch]$Desktop, [switch]$Check)
$ErrorActionPreference = 'Stop'
$TaskRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $TaskRoot '.venv\Scripts\python.exe'
$Frontend = Join-Path $TaskRoot 'frontend'
$DevRoot = Join-Path $TaskRoot 'build\dev-workspace'
if (-not (Test-Path -LiteralPath $Python)) { throw 'Missing .venv Python; install project dependencies first.' }
if ($Check) { Write-Output "Frontend: http://127.0.0.1:5181/`nBackend: http://127.0.0.1:8011/api`nData: $DevRoot\data"; exit 0 }
foreach ($port in @(8011,5181)) { if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { throw "Port $port is occupied; stop the previous development session first." } }
New-Item -ItemType Directory -Path $DevRoot -Force | Out-Null
$ScopedNames = @('VITE_API_URL','YUS_AI_DEV_DATA_DIR','YUS_AI_EXTERNAL_BACKEND','YUS_AI_DATA_DIR','YUS_AI_LOG_DIR','ARGOS_PACKAGES_DIR','ARGOS_CHUNK_TYPE','XDG_DATA_HOME','XDG_CONFIG_HOME','XDG_CACHE_HOME')
$PreviousEnvironment = @{}
foreach ($name in $ScopedNames) { $PreviousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name,'Process') }
$env:YUS_AI_DATA_DIR = Join-Path $DevRoot 'data'
$env:YUS_AI_LOG_DIR = Join-Path $DevRoot 'logs'
$env:ARGOS_PACKAGES_DIR = Join-Path $DevRoot 'translation-models'
$env:ARGOS_CHUNK_TYPE = 'MINISBD'
$env:XDG_DATA_HOME = Join-Path $DevRoot 'translation-runtime\data'
$env:XDG_CONFIG_HOME = Join-Path $DevRoot 'translation-runtime\config'
$env:XDG_CACHE_HOME = Join-Path $DevRoot 'translation-runtime\cache'
$env:VITE_API_URL = 'http://127.0.0.1:8011/api'
$env:YUS_AI_DEV_DATA_DIR = $env:YUS_AI_DATA_DIR
$env:YUS_AI_EXTERNAL_BACKEND = '1'
$backend = $null
try {
  $backend = Start-Process -FilePath $Python -ArgumentList @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8011','--reload','--reload-dir','app') -WorkingDirectory (Join-Path $TaskRoot 'backend') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $DevRoot 'backend.stdout.log') -RedirectStandardError (Join-Path $DevRoot 'backend.stderr.log')
  Write-Output "Development data: $($env:YUS_AI_DATA_DIR). Ctrl+C stops this session."
  Push-Location $Frontend
  try {
    if ($Desktop) { npm run desktop:dev -- --config ../scripts/tauri.dev.json }
    else { npm run dev -- --host 127.0.0.1 --port 5181 --strictPort }
  } finally { Pop-Location }
} finally {
  # Stop only the process tree started by this script, never the installed application.
  if ($backend -and -not $backend.HasExited) { & taskkill /PID $backend.Id /T /F | Out-Null }
  foreach ($name in $ScopedNames) { [Environment]::SetEnvironmentVariable($name,$PreviousEnvironment[$name],'Process') }
}
