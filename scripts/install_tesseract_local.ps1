$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$downloadDir = Join-Path $root "tools\downloads"
$targetDir = "C:\temp\Tesseract-OCR"
$installerPath = Join-Path $downloadDir "tesseract-ocr-w64-setup-5.5.0.20241111.exe"
$url = "https://github.com/tesseract-ocr/tesseract/releases/download/5.5.0/tesseract-ocr-w64-setup-5.5.0.20241111.exe"

New-Item -ItemType Directory -Force $downloadDir | Out-Null
New-Item -ItemType Directory -Force $targetDir | Out-Null

if (-not (Test-Path $installerPath)) {
  Invoke-WebRequest -Uri $url -OutFile $installerPath
}

Write-Host "Installing Tesseract to $targetDir"
& $installerPath /S /D=$targetDir

$exePath = Join-Path $targetDir "tesseract.exe"
if (Test-Path $exePath) {
  Write-Host "Tesseract installed: $exePath"
  Write-Host "Set environment variable:"
  Write-Host "`$env:TESSERACT_CMD='$exePath'"
  exit 0
}

Write-Host "Tesseract installer completed but executable was not found."
Write-Host "Run this script from an elevated PowerShell if the installer could not write to the target directory."
exit 1
