param (
    [Parameter(Mandatory = $true)]
    [string]$InputFile,
    [string]$ProjectRoot = (Get-Item -Path (Join-Path $PSScriptRoot "..")).FullName,
    [string]$SourceName = "carga_mvp",
    [string]$TestQuery = "jurisprudencia del articulo 10 de la LIR",
    [switch]$StrictValidation
)

$ErrorActionPreference = "Stop"

function Write-Log {
    param (
        [string]$Message,
        [string]$Level = "INFO"
    )
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "$ts [$Level] $Message"
}

if (-not (Test-Path $InputFile)) {
    throw "No existe InputFile: $InputFile"
}

$resolvedInput = (Resolve-Path $InputFile).Path
$reportDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $reportDir)) {
    New-Item -ItemType Directory -Path $reportDir | Out-Null
}
$reportFile = Join-Path $reportDir ("validacion_sii_admin_" + (Get-Date -Format "yyyy-MM-dd_HHmmss") + ".json")

Write-Log "Proyecto: $ProjectRoot"
Write-Log "Input: $resolvedInput"
Write-Log "Reporte validacion: $reportFile"

Push-Location $ProjectRoot
try {
    $venvPath = Join-Path $ProjectRoot ".venv"
    if (Test-Path $venvPath) {
        Write-Log "Activando entorno virtual: $venvPath"
        & "$venvPath\Scripts\Activate.ps1"
    } else {
        Write-Log "No se encontro .venv, usando Python del sistema." "WARN"
    }

    $env:PYTHONIOENCODING = "utf-8"

    # 1) Validacion
    $validateArgs = @(
        "main.py", "validate-sii-admin", $resolvedInput,
        "--report-file", $reportFile
    )
    if ($StrictValidation) {
        $validateArgs += "--strict"
    }
    Write-Log ("Ejecutando: python " + ($validateArgs -join " "))
    & python @validateArgs
    if ($LASTEXITCODE -ne 0) {
        throw "validate-sii-admin fallo con codigo $LASTEXITCODE"
    }

    # 2) Import
    $importArgs = @(
        "main.py", "import-sii-admin", $resolvedInput,
        "--source-name", $SourceName
    )
    Write-Log ("Ejecutando: python " + ($importArgs -join " "))
    & python @importArgs
    if ($LASTEXITCODE -ne 0) {
        throw "import-sii-admin fallo con codigo $LASTEXITCODE"
    }

    # 3) Ingest
    Write-Log "Ejecutando: python main.py ingest"
    & python "main.py" "ingest"
    if ($LASTEXITCODE -ne 0) {
        throw "ingest fallo con codigo $LASTEXITCODE"
    }

    # 4) Query de prueba
    Write-Log "Ejecutando query de prueba: $TestQuery"
    & python "main.py" "query" $TestQuery
    if ($LASTEXITCODE -ne 0) {
        throw "query de prueba fallo con codigo $LASTEXITCODE"
    }

    Write-Log "Flujo MVP completado correctamente." "OK"
    Write-Log "Siguiente paso: probar en Telegram con una pregunta equivalente."
    exit 0
}
catch {
    Write-Log $_.Exception.Message "ERROR"
    exit 1
}
finally {
    Pop-Location
}

