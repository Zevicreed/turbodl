@echo off
powershell -NoProfile -ExecutionPolicy Bypass -Command "Remove-Item -ErrorAction SilentlyContinue ([IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), 'TurboDL.lnk')); Remove-Item -ErrorAction SilentlyContinue ([IO.Path]::Combine([Environment]::GetFolderPath('Startup'), 'TurboDL.lnk')); Write-Host 'Accesos de TurboDL eliminados: ya no arranca con Windows.'"
pause
