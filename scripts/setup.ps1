# Text2CAD one-step installer (Windows).
# Installs/updates: Python (if needed), Ollama (if needed), the local AI model, Python packages,
# runs a self-test and creates a desktop shortcut. Safe to run again at any time.

param(
    [string]$Model = ""   # override the AI model, e.g. -Model qwen2.5-coder:7b
)

# "Continue": native tools (py, pip, ollama, winget) write progress to stderr, which "Stop" would treat
# as fatal in Windows PowerShell 5.1. Failures are detected via $LASTEXITCODE and explicit checks instead.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "    $text" -ForegroundColor Green }
function Warn($text) { Write-Host "    $text" -ForegroundColor Yellow }
function Fail($text) { Write-Host ""; Write-Host "ERROR: $text" -ForegroundColor Red; exit 1 }

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}

function Has-Winget { return [bool](Get-Command winget -ErrorAction SilentlyContinue) }

Write-Host "Text2CAD installer" -ForegroundColor White
Write-Host "Project folder: $Root"

# ------------------------------------------------------------------ 1. Python
Step "Checking for a compatible Python (3.10 - 3.13)"
$Python = $null
foreach ($v in @("3.12", "3.11", "3.13", "3.10")) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $exe = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe) { $Python = $exe.Trim(); break }
    }
    $candidate = Join-Path $env:LOCALAPPDATA ("Programs\Python\Python" + $v.Replace(".", "") + "\python.exe")
    if (Test-Path $candidate) { $Python = $candidate; break }
}
if (-not $Python) {
    Warn "No compatible Python found. Installing Python 3.12..."
    if (-not (Has-Winget)) { Fail "winget is not available. Install Python 3.12 from https://www.python.org/downloads/ and run install.bat again." }
    winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
    Refresh-Path
    $Python = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    if (-not (Test-Path $Python)) { Fail "Python installation did not complete. Install Python 3.12 manually and run install.bat again." }
}
Ok "Using $Python"

# ------------------------------------------------------------------ 2. Ollama
Step "Checking for Ollama (runs the AI model locally)"
$Ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if (-not $Ollama) {
    $candidate = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (Test-Path $candidate) { $Ollama = $candidate }
}
if (-not $Ollama) {
    Warn "Ollama not found. Installing..."
    if (-not (Has-Winget)) { Fail "winget is not available. Install Ollama from https://ollama.com/download and run install.bat again." }
    winget install -e --id Ollama.Ollama --accept-package-agreements --accept-source-agreements
    Refresh-Path
    $Ollama = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (-not (Test-Path $Ollama)) { Fail "Ollama installation did not complete. Install it from https://ollama.com/download and run install.bat again." }
}
Ok "Using $Ollama"

function Test-Ollama {
    try { Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/version" -TimeoutSec 5 | Out-Null; return $true } catch { return $false }
}
if (-not (Test-Ollama)) {
    Warn "Starting the Ollama server..."
    Start-Process -FilePath $Ollama -ArgumentList "serve" -WindowStyle Hidden
    for ($i = 0; $i -lt 30 -and -not (Test-Ollama); $i++) { Start-Sleep -Seconds 1 }
    if (-not (Test-Ollama)) { Fail "Could not start Ollama. Open the Ollama app manually and run install.bat again." }
}
Ok "Ollama server is running"

# ------------------------------------------------------------------ 3. AI model
Step "Choosing the AI model for this PC"
$ramGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
$vramGB = 0
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    $mem = (& nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>$null | Select-Object -First 1)
    if ($mem) { $vramGB = [math]::Round([double]$mem / 1024) }
}
if (-not $Model) {
    if (($ramGB + $vramGB) -ge 28 -and $ramGB -ge 16) { $Model = "qwen3-coder:30b" } else { $Model = "qwen2.5-coder:7b" }
}
Ok "RAM: $ramGB GB, GPU memory: $vramGB GB -> model: $Model"

$installed = & $Ollama list 2>$null | Out-String
if ($installed -match [regex]::Escape($Model)) {
    Ok "$Model is already downloaded"
} else {
    Warn "Downloading $Model (this can take a while: ~19 GB for qwen3-coder:30b, ~5 GB for qwen2.5-coder:7b)..."
    & $Ollama pull $Model
    if ($LASTEXITCODE -ne 0) { Fail "Model download failed. Check your internet connection and run install.bat again." }
}

# ------------------------------------------------------------------ 4. Python environment
Step "Setting up the Python environment"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    & $VenvPython -c "import sys; assert (3, 10) <= sys.version_info[:2] <= (3, 13)" 2>$null
    if ($LASTEXITCODE -ne 0) { Warn "Existing .venv uses an unsupported Python; recreating it."; Remove-Item -Recurse -Force (Join-Path $Root ".venv") }
}
if (-not (Test-Path $VenvPython)) { & $Python -m venv (Join-Path $Root ".venv") }
& $VenvPython -m pip install --upgrade pip --quiet
Warn "Installing packages (CadQuery, Qt, VTK, Ollama client...). First run downloads ~700 MB."
& $VenvPython -m pip install -r (Join-Path $Root "requirements.txt") --quiet
if ($LASTEXITCODE -ne 0) { Fail "Package installation failed (see messages above)." }
Ok "Packages installed"

# ------------------------------------------------------------------ 5. Configuration
Step "Writing configuration (.env)"
$EnvFile = Join-Path $Root ".env"
if (-not (Test-Path $EnvFile)) { Copy-Item (Join-Path $Root ".env.example") $EnvFile }
$lines = Get-Content $EnvFile | Where-Object { $_ -notmatch "^OLLAMA_MODEL=" }
$lines += "OLLAMA_MODEL=$Model"
Set-Content -Path $EnvFile -Value $lines -Encoding ASCII
Ok "OLLAMA_MODEL=$Model"

# ------------------------------------------------------------------ 6. Self-test
Step "Running self-test (builds every part template)"
& $VenvPython (Join-Path $Root "tests\test_templates.py") | Select-String -Pattern "FAIL|failure"
if ($LASTEXITCODE -ne 0) { Fail "Self-test failed (see messages above)." }
& $VenvPython -c "import text2cad.gui"
if ($LASTEXITCODE -ne 0) { Fail "The desktop app failed to load (see messages above)." }
Ok "Self-test passed"

# ------------------------------------------------------------------ 7. Shortcuts
Step "Creating desktop and Start menu shortcuts"
$Icon = Join-Path $Root "assets\text2cad.ico"
if (-not (Test-Path $Icon)) { & $VenvPython (Join-Path $Root "scripts\make_icon.py") | Out-Null }
$Shell = New-Object -ComObject WScript.Shell
$StartMenu = Join-Path ([Environment]::GetFolderPath("Programs")) "Text2CAD.lnk"
foreach ($path in @((Join-Path ([Environment]::GetFolderPath("Desktop")) "Text2CAD.lnk"), $StartMenu)) {
    $Link = $Shell.CreateShortcut($path)
    $Link.TargetPath = Join-Path $Root ".venv\Scripts\pythonw.exe"   # pythonw = no console window
    $Link.Arguments = "-m text2cad.gui"
    $Link.WorkingDirectory = $Root
    $Link.IconLocation = $Icon
    $Link.Description = "Text2CAD - words to engineering 3D models"
    $Link.Save()
}
Ok "Shortcut 'Text2CAD' added to your desktop and Start menu"

Write-Host ""
Write-Host "Installation complete. Open 'Text2CAD' from your desktop or Start menu." -ForegroundColor Green
