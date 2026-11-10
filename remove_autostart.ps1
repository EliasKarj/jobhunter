# Poistaa automaattisen käynnistyksen. Jo käynnissä oleva sovellus sammuu uloskirjautuessa tai Tehtävienhallinnasta (pythonw.exe).
Unregister-ScheduledTask -TaskName "Jobhunter" -Confirm:$false
Write-Host "Automaattinen käynnistys poistettu."
