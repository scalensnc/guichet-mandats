param(
    [switch]$DepuisCsv,
    [string]$Message = "Mise à jour des mandats"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $ProjectRoot

try {
    if (-not $DepuisCsv -and -not (Test-Path Env:BEXIO_API_TOKEN)) {
        throw "BEXIO_API_TOKEN n'est pas définie. Utilisez -DepuisCsv pour publier le CSV existant."
    }

    $Arguments = @(".\update_portal.py")
    if ($DepuisCsv) {
        $Arguments += "--from-csv"
    }

    & python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "La mise à jour du portail a échoué."
    }

    git add -- portal/data/projects.json
    git diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Aucune modification à publier."
        exit 0
    }

    git commit -m $Message
    if ($LASTEXITCODE -ne 0) {
        throw "La création du commit a échoué."
    }

    git push origin main
    if ($LASTEXITCODE -ne 0) {
        throw "L'envoi vers GitHub a échoué."
    }

    Write-Host "Mise à jour envoyée. GitHub Pages va republier le portail."
}
finally {
    Pop-Location
}

