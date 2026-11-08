# TurboDL — instalación de accesos directos y arranque con Windows.
# No mueve ni copia nada: los accesos apuntan a esta misma carpeta
# (la extensión de Chrome ya vive aquí y sigue intacta).
$ErrorActionPreference = 'Stop'
$proj = Split-Path -Parent $MyInvocation.MyCommand.Path
$pyw = Join-Path $proj '.venv\Scripts\pythonw.exe'
$app = Join-Path $proj 'app.py'
$ico = Join-Path $proj 'TurboDL.ico'

if (-not (Test-Path $pyw)) {
    Write-Host 'No encontré el entorno de TurboDL (.venv). Revisa que este script esté en la carpeta del programa.'
    exit 1
}

$shell = New-Object -ComObject WScript.Shell

function New-TurboShortcut($path, $extraArgs) {
    $sc = $shell.CreateShortcut($path)
    $sc.TargetPath = $pyw
    $sc.Arguments = '"' + $app + '"' + $extraArgs
    $sc.WorkingDirectory = $proj
    if (Test-Path $ico) { $sc.IconLocation = $ico }
    $sc.Description = 'TurboDL - Descargador'
    $sc.Save()
}

$desktop = [Environment]::GetFolderPath('Desktop')
New-TurboShortcut (Join-Path $desktop 'TurboDL.lnk') ''

$startup = [Environment]::GetFolderPath('Startup')
New-TurboShortcut (Join-Path $startup 'TurboDL.lnk') ' --inicio'

Write-Host ''
Write-Host 'TurboDL instalado:'
Write-Host ' - Icono "TurboDL" en el Escritorio (doble clic, sin consola).'
Write-Host ' - Arranca solo al encender la PC, minimizado, ya escuchando a la extensión.'
Write-Host 'Para quitar el arranque automático, usa "Quitar TurboDL del inicio.bat".'
