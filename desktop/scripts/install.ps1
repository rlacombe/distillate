# install.ps1 — Install Distillate CLI + desktop app on Windows
# Run from PowerShell: .\scripts\install.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DesktopDir = Split-Path -Parent $ScriptDir
$ProjectDir = Split-Path -Parent $DesktopDir

$AppName = "Distillate"
$VenvDir = Join-Path $env:LOCALAPPDATA "$AppName\python-env"

# ── 1. Install Python CLI into the app's venv ──

Write-Host "==> Setting up Python environment..."

# Check for uv
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "    Installing uv..."
    irm https://astral.sh/uv/install.ps1 | iex
    $env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
}

# Always recreate venv to ensure correct Python version
if (Test-Path $VenvDir) {
    Remove-Item -Recurse -Force $VenvDir
}
Write-Host "    Creating venv..."
uv venv --python 3.12 $VenvDir

Write-Host "    Installing distillate..."
$env:VIRTUAL_ENV = $VenvDir
uv pip install -e "$ProjectDir[desktop]"

# ── 2. Install npm dependencies ──

Write-Host "==> Installing npm dependencies..."
Push-Location $DesktopDir
npm install

# ── 3. Build Electron app ──

Write-Host "==> Building $AppName..."
npm run build:win

Pop-Location

# ── 4. Create Start Menu shortcut ──

$ExePath = Join-Path $DesktopDir "dist\win-unpacked\$AppName.exe"
if (-not (Test-Path $ExePath)) {
    # NSIS installer — run it
    $Installer = Get-ChildItem (Join-Path $DesktopDir "dist") -Filter "*.exe" | Select-Object -First 1
    if ($Installer) {
        Write-Host "==> Running installer..."
        Start-Process -Wait $Installer.FullName
    } else {
        Write-Host "Error: build output not found in dist/."
        exit 1
    }
} else {
    # Portable build — create shortcut
    $StartMenu = [System.IO.Path]::Combine($env:APPDATA, "Microsoft\Windows\Start Menu\Programs")
    $ShortcutPath = Join-Path $StartMenu "$AppName.lnk"

    Write-Host "==> Creating Start Menu shortcut..."
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $ExePath
    $Shortcut.WorkingDirectory = Split-Path $ExePath
    $Shortcut.Description = "$AppName — Your research alchemist"
    $IconPath = Join-Path $DesktopDir "resources\icon.ico"
    if (Test-Path $IconPath) { $Shortcut.IconLocation = $IconPath }
    $Shortcut.Save()
}

# ── 5. Launch ──

Write-Host "==> Launching $AppName..."
if (Test-Path $ExePath) {
    Start-Process $ExePath
} else {
    # NSIS installer handles launch
    Write-Host "    $AppName installed via installer."
}

Write-Host "Done!"
