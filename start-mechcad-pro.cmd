@echo off
setlocal
set "ROOT=%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%ROOT%scripts\mechcad-tray.ps1" %*
endlocal
