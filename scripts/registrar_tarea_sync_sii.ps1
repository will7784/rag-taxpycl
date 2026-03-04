param(
    [string]$TaskName = "RAG-SII-Sync-Diario",
    [string]$Time = "03:15",
    [string]$ProjectRoot = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

$scriptPath = Join-Path $ProjectRoot "scripts\sync_sii_diario.ps1"
if (-not (Test-Path $scriptPath)) {
    throw "No se encontró el script: $scriptPath"
}

$actionArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -ProjectRoot `"$ProjectRoot`""
Write-Host "Registrando tarea '$TaskName' a las $Time..."
Write-Host "Acción: powershell.exe $actionArgs"

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $actionArgs
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable

# Registrar para el usuario actual (sin elevar privilegios).
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

Write-Host "Tarea registrada correctamente."
Write-Host "Puedes verificar con:"
Write-Host "  Get-ScheduledTask -TaskName `"$TaskName`" | Format-List"
