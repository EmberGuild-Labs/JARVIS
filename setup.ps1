# One-time setup: virtualenv, dependencies, model downloads.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
if (-not (Test-Path .venv)) { py -3.13 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --upgrade pip -q
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -c "import openwakeword.utils as u; u.download_models(model_names=['hey_jarvis'])"
.\.venv\Scripts\python.exe -c "from jarvis.speech import ensure_speaker_model; ensure_speaker_model()"
if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "Created .env - put your Anthropic API key in it." }
Write-Host "`nNext: .\.venv\Scripts\python.exe -m jarvis.enroll   (record your voice)"
