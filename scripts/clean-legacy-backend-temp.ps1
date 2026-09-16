param(
  [Parameter(Mandatory=$true)][string]$TempDirectory,
  [Parameter(Mandatory=$true)][string]$InstallDirectory,
  [switch]$Clean
)
$ErrorActionPreference = 'Stop'
$tempRoot = (Resolve-Path -LiteralPath $TempDirectory).Path.TrimEnd('\')
$installRoot = (Resolve-Path -LiteralPath $InstallDirectory).Path.TrimEnd('\')
if ((Split-Path -Leaf $tempRoot) -ne 'Temp') { throw 'Expected an explicit Temp directory.' }
$backendProcesses = @(Get-Process -Name 'yus-ai-backend' -ErrorAction SilentlyContinue)
$activeRoots = @()
foreach ($backendProcess in $backendProcesses) {
  if ($backendProcess.Path -ne "$installRoot\yus-ai-backend.exe") { continue }
  foreach ($module in $backendProcess.Modules) {
    if ($module.FileName -match '\\(_MEI[0-9a-fA-F]+)\\') {
      $activeRoots += Join-Path $tempRoot $Matches[1]
    }
  }
}
$activeRoots = @($activeRoots | Sort-Object -Unique)
$referenceRoot = if ($activeRoots.Count) { $activeRoots[0] } else { Join-Path $installRoot 'backend-runtime' }
if (!(Test-Path -LiteralPath $referenceRoot -PathType Container)) {
  throw 'No verified runtime reference found; refusing broad cleanup.'
}
$fingerprintFiles = @('python310.dll', 'pydantic_core\_pydantic_core.cp310-win_amd64.pyd')
$fingerprints = @{}
foreach ($relativeFile in $fingerprintFiles) {
  $fingerprints[$relativeFile] = (Get-FileHash -LiteralPath (Join-Path $referenceRoot $relativeFile)).Hash
}
$startupTimes = @(Get-Content -LiteralPath "$installRoot\logs\yus-ai.log" | ForEach-Object {
  if ($_ -match '^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*backend_started') {
    [datetime]::ParseExact($Matches[1], 'yyyy-MM-dd HH:mm:ss', $null)
  }
})
$freedBytes = 0L
foreach ($folder in Get-ChildItem -LiteralPath $tempRoot -Directory -Force -Filter '_MEI*') {
  $candidate = (Resolve-Path -LiteralPath $folder.FullName).Path
  if ($folder.Name -notmatch '^_MEI[0-9a-fA-F]+$' -or
      (Split-Path -Parent $candidate) -ne $tempRoot -or
      ($folder.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
      $candidate -in $activeRoots) { continue }
  # Require both this application's recorded startup AND matching runtime binaries.
  $matchesStartup = @($startupTimes | Where-Object {
    $delta = ($_ - $folder.CreationTime).TotalSeconds
    $delta -ge -1 -and $delta -le 20
  }).Count -gt 0
  if (!$matchesStartup) { continue }
  $matchesRuntime = $true
  foreach ($relativeFile in $fingerprintFiles) {
    $filePath = Join-Path $candidate $relativeFile
    if (!(Test-Path -LiteralPath $filePath) -or
        (Get-FileHash -LiteralPath $filePath).Hash -ne $fingerprints[$relativeFile]) {
      $matchesRuntime = $false; break
    }
  }
  if (!$matchesRuntime) { continue }
  $files = @(Get-ChildItem -LiteralPath $candidate -Recurse -Force)
  if (@($files | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count) { continue }
  # Probe exclusive access to every file before deleting: skip any in-use directory.
  $unused = $true
  foreach ($file in $files | Where-Object { !$_.PSIsContainer }) {
    try {
      $probe = [IO.File]::Open($file.FullName, 'Open', 'Read', 'None')
      $probe.Dispose()
    } catch { $unused = $false; break }
  }
  if (!$unused) { continue }
  $bytes = ($files | Where-Object { !$_.PSIsContainer } | Measure-Object Length -Sum).Sum
  [pscustomobject]@{Path=$candidate;GiB=[math]::Round($bytes/1GB,3);Action=$(if($Clean){'Delete'}else{'Preview'})} | ConvertTo-Json -Compress
  if ($Clean) {
    Remove-Item -LiteralPath $candidate -Recurse -Force
    $freedBytes += $bytes
  }
}
[pscustomobject]@{FreedGiB=[math]::Round($freedBytes/1GB,2);KeptActive=$activeRoots} | ConvertTo-Json -Compress
