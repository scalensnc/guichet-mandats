param(
    [switch]$DepuisCsv,
    [string]$Message = "Mise à jour des mandats"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $ProjectRoot

try {
    # Utilise Git du système lorsqu'il est dans le PATH. Sur un poste équipé de
    # Codex Desktop, Git peut aussi être fourni avec l'application sans être
    # visible depuis une fenêtre PowerShell classique.
    $GitCommand = Get-Command git -ErrorAction SilentlyContinue
    if ($GitCommand) {
        $GitExecutable = $GitCommand.Source
    }
    else {
        $GitCandidates = @(
            (Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe"),
            (Join-Path $env:ProgramFiles "Git\cmd\git.exe"),
            (Join-Path ${env:ProgramFiles(x86)} "Git\cmd\git.exe"),
            (Join-Path $env:LOCALAPPDATA "Programs\Git\cmd\git.exe")
        )
        $GitExecutable = $GitCandidates |
            Where-Object { $_ -and (Test-Path -LiteralPath $_) } |
            Select-Object -First 1
    }

    if (-not $GitExecutable) {
        throw "Git est introuvable. Installez Git pour Windows depuis https://git-scm.com/download/win puis rouvrez PowerShell."
    }

    Write-Host "Git utilisé : $GitExecutable"

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

    & $GitExecutable add -- portal/data/projects.json
    & $GitExecutable diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Aucune modification à publier."
        exit 0
    }

    & $GitExecutable commit -m $Message
    if ($LASTEXITCODE -ne 0) {
        throw "La création du commit a échoué."
    }

    & $GitExecutable push origin main
    if ($LASTEXITCODE -ne 0) {
        throw "L'envoi vers GitHub a échoué."
    }

    Write-Host "Mise à jour envoyée. GitHub Pages va republier le portail."
}
finally {
    Pop-Location
}
