# =============================================================================
# Spire Oracle — one-shot setup for Slay the Spire 2 advisor
# Run this once. Double-click install.bat, or:  powershell -ExecutionPolicy Bypass -File setup.ps1
# =============================================================================
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Spire Oracle setup" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

# 1. Python ------------------------------------------------------------------
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
  Write-Host "`n[X] Python not found." -ForegroundColor Red
  Write-Host "    Install Python 3.11+ from https://www.python.org/downloads/" -ForegroundColor Yellow
  Write-Host "    IMPORTANT: tick 'Add python.exe to PATH' in the installer, then re-run this." -ForegroundColor Yellow
  Read-Host "Press Enter to exit"; exit 1
}
Write-Host "`n[OK] Python: $(python --version 2>&1)" -ForegroundColor Green

# 2. Claude Code CLI ---------------------------------------------------------
$claude = Get-Command claude -ErrorAction SilentlyContinue
if (-not $claude) {
  Write-Host "[!] 'claude' CLI not found." -ForegroundColor Yellow
  Write-Host "    The advisor needs Claude Code (your subscription, no API key)." -ForegroundColor Yellow
  Write-Host "    Install + sign in: https://claude.com/claude-code" -ForegroundColor Yellow
} else {
  Write-Host "[OK] Claude Code CLI: found" -ForegroundColor Green
}

# 3. Python dependencies -----------------------------------------------------
Write-Host "`nInstalling Python dependencies..." -ForegroundColor Cyan
python -m pip install --upgrade pip 2>$null | Out-Null
python -m pip install -r (Join-Path $root "requirements.txt")
Write-Host "[OK] Dependencies installed" -ForegroundColor Green

# 4. Card/relic dataset ------------------------------------------------------
Write-Host "`nDownloading StS2 card/relic dataset..." -ForegroundColor Cyan
python (Join-Path $root "data\fetch_data.py")

# 5. Validate the Claude Code CLI invocation ---------------------------------
if ($claude) {
  Write-Host "`nValidating Claude Code CLI (calls the model once)..." -ForegroundColor Cyan
  try { python -m bridge.model_client --selftest }
  catch { Write-Host "[!] Selftest issue; re-run later: python -m bridge.model_client --selftest" -ForegroundColor Yellow }
}

# 6. Is the game bridge reachable? ------------------------------------------
Write-Host "`nLooking for the STS2MCP mod (only visible while StS2 is running)..." -ForegroundColor Cyan
try {
  $r = Invoke-RestMethod "http://127.0.0.1:15526/" -TimeoutSec 2
  Write-Host "[OK] STS2MCP detected: $($r.message)" -ForegroundColor Green
} catch {
  Write-Host "[i] Not detected. That's fine if StS2 isn't running right now." -ForegroundColor Yellow
  Write-Host "    Make sure the STS2MCP mod is installed + enabled: https://github.com/Gennadiyev/STS2MCP" -ForegroundColor Yellow
}

# 7. Auto-launch with the game? ---------------------------------------------
Write-Host ""
$ans = Read-Host "Auto-launch the advisor whenever StS2 runs? (recommended) [Y/n]"
if ($ans -ne 'n' -and $ans -ne 'N') {
  & (Join-Path $root "autostart\install_autostart.ps1")
  Start-Process pythonw -ArgumentList ('"' + (Join-Path $root "autostart\supervisor.py") + '"') -WorkingDirectory $root
  Write-Host "`n[OK] Auto-launch installed + running. Just play StS2 and the overlay appears on decision screens." -ForegroundColor Green
} else {
  Write-Host "`n[OK] Skipped auto-launch. Use start.bat to run the advisor manually each time." -ForegroundColor Green
}

Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host "  Setup complete." -ForegroundColor Cyan
Write-Host "  Prereqs: StS2 + STS2MCP mod + Claude Code (signed in)." -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Read-Host "Press Enter to close"
