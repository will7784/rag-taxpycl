param(
    [string]$ProjectRoot = "",
    [int]$CuerpoId = 2,
    [int]$MaxArticulos = 300,
    [int]$MaxPronunciamientos = 2000,
    [switch]$DownloadPdf
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

$env:PYTHONIOENCODING = "utf-8"

$logsDir = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Path $logsDir -Force | Out-Null

$timestamp = Get-Date -Format "yyyy-MM-dd"
$logFile = Join-Path $logsDir ("sync_sii_diario_" + $timestamp + ".log")

function Write-Log {
    param([string]$Text)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Text
    $line | Tee-Object -FilePath $logFile -Append
}

Set-Location $ProjectRoot
Write-Log "Inicio tarea diaria SII incremental."
Write-Log ("Proyecto: {0}" -f $ProjectRoot)

$syncArgs = @(
    "main.py",
    "sync-sii",
    "--cuerpo-id", "$CuerpoId",
    "--max-articulos", "$MaxArticulos",
    "--max-pronunciamientos", "$MaxPronunciamientos",
    "--incremental"
)

if ($DownloadPdf.IsPresent) {
    $syncArgs += "--download-pdf"
}

Write-Log ("Ejecutando: python {0}" -f ($syncArgs -join " "))
& python @syncArgs 2>&1 | Tee-Object -FilePath $logFile -Append
$syncExit = $LASTEXITCODE
if ($syncExit -ne 0) {
    Write-Log ("ERROR: sync-sii terminó con código {0}" -f $syncExit)
    exit $syncExit
}

$ingestArgs = @("main.py", "ingest")
Write-Log ("Ejecutando: python {0}" -f ($ingestArgs -join " "))
& python @ingestArgs 2>&1 | Tee-Object -FilePath $logFile -Append
$ingestExit = $LASTEXITCODE
if ($ingestExit -ne 0) {
    Write-Log ("ERROR: ingest terminó con código {0}" -f $ingestExit)
    exit $ingestExit
}

Write-Log "Tarea diaria completada correctamente."
exit 0
