$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { throw "python not found; install Python 3 and add it to PATH" }
& $py tools\make_icon.py
if (-not (Test-Path "assets\app.ico")) { throw "assets/app.ico was not generated" }
& $py -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name Perfmon --icon assets\app.ico --add-data "web;web" `
    --hidden-import clr --hidden-import webview.platforms.edgechromium `
    --hidden-import webview.platforms.winforms app.py
Copy-Item "dist\Perfmon.exe" "Perfmon.exe" -Force
Remove-Item build, dist, Perfmon.spec -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "Done: $PSScriptRoot\Perfmon.exe"
