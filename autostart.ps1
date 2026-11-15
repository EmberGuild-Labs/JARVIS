# Enable or disable JARVIS starting when you sign in to Windows.
#   .\autostart.ps1          -> enable
#   .\autostart.ps1 -Remove  -> disable
param([switch]$Remove)
$key  = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($Remove) {
    Remove-ItemProperty -Path $key -Name "JARVIS" -ErrorAction SilentlyContinue
    Write-Host "JARVIS autostart disabled."
} else {
    $cmd = "`"$root\.venv\Scripts\pythonw.exe`" `"$root\run.pyw`" --startup"
    Set-ItemProperty -Path $key -Name "JARVIS" -Value $cmd
    Write-Host "JARVIS will start automatically when you sign in."
}
