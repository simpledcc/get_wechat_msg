param(
  [switch]$DryRun,
  [string]$OutDir = "",
  [string]$OutRoot = "D:\demo\wechat_info",
  [string]$PythonExe = "python",
  [string]$HdcPath = "",
  [string]$Target = ""
)

$ErrorActionPreference = "Stop"

$ToolDir = $PSScriptRoot
$Exporter = Join-Path $ToolDir "wechat_hdc_export_0630.py"
if ([string]::IsNullOrWhiteSpace($OutDir)) {
  $RunStamp = Get-Date -Format "yyyyMMdd_HHmmss"
  $OutDir = Join-Path $OutRoot $RunStamp
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Write-Host "Output directory: $OutDir"

if ($DryRun) {
  Write-Host "Dry run: launcher parsed successfully. Python exporter was not started."
  exit 0
}

$ExporterArgs = @(
  $Exporter,
  "--transport", "hdc",
  "--mode", "scan-list",
  "--out", $OutDir,
  "--crop", "auto",
  "--max-list-swipes", "20",
  "--max-folder-swipes", "20",
  "--max-list-top-swipes", "8",
  "--max-seek", "0",
  "--max-shots", "500",
  "--stable-count", "4",
  "--wait", "1.0",
  "--velocity", "900",
  "--seek-velocity", "550",
  "--seek-wait", "1.0",
  "--seek-confirm-wait", "1.0",
  "--seek-swipe-mode", "fling",
  "--seek-fling-count", "1",
  "--seek-fling-velocity", "6000",
  "--seek-fling-step-length", "0",
  "--seek-fling-gap", "0.0",
  "--capture-velocity", "900",
  "--capture-wait", "1.0"
)

if (-not [string]::IsNullOrWhiteSpace($HdcPath)) {
  $ExporterArgs += @("--hdc", $HdcPath)
}
if (-not [string]::IsNullOrWhiteSpace($Target)) {
  $ExporterArgs += @("--target", $Target)
}

& $PythonExe @ExporterArgs
exit $LASTEXITCODE
