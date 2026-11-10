# Käynnistää Työnhaku-webappin taustalla aina kun kirjaudut Windowsiin (ei konsoli-ikkunaa).
# Aja PowerShellissä tässä kansiossa:  powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1
# Selaimessa: http://127.0.0.1:8765   Loki: data\jobhunter.log   Poisto: remove_autostart.ps1
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $py) {
    $p = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    if ($p) { $py = Join-Path (Split-Path $p) "pythonw.exe" }
}
if (-not $py -or -not (Test-Path $py)) {
    Write-Host "pythonw.exe ei löytynyt. Asenna Python ja varmista, että se on PATHissa."
    exit 1
}
$action = New-ScheduledTaskAction -Execute $py -Argument "webapp.py --no-browser" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "Jobhunter" -Action $action -Trigger $trigger -Settings $settings `
    -Description "Työnhaku-webapp taustalla" -Force | Out-Null
Start-ScheduledTask -TaskName "Jobhunter"
Write-Host "Valmis. Työnhaku käynnistyy jatkossa kirjautuessasi ja on osoitteessa http://127.0.0.1:8765"
