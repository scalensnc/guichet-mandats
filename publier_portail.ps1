param(
    [switch]$DepuisCsv,
    [string]$Message = "Mise a jour des mandats"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $ProjectRoot

try {
    # Utilise Git du systeme lorsqu'il est dans le PATH. Sur un poste equipe de
    # Codex Desktop, Git peut aussi etre fourni avec l'application sans etre
    # visible depuis une fenetre PowerShell classique.
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

    Write-Host "Git utilise : $GitExecutable"

    function Invoke-ProjectGit {
        param(
            [Parameter(Mandatory = $true)]
            [string[]]$Arguments,
            [int[]]$AllowedExitCodes = @(0)
        )

        # Le depot a initialement ete cree par Codex. La configuration locale
        # safe.directory evite qu'une autre session Windows refuse de l'utiliser
        # uniquement a cause du proprietaire inscrit sur le dossier .git.
        $PreviousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $GitOutput = & $GitExecutable -c "safe.directory=$ProjectRoot" @Arguments 2>&1
            $GitExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $PreviousErrorActionPreference
        }

        if ($GitOutput) {
            $GitOutput | ForEach-Object { Write-Host $_ }
        }
        if ($AllowedExitCodes -notcontains $GitExitCode) {
            $CommandText = "git " + ($Arguments -join " ")
            throw "$CommandText a echoue avec le code $GitExitCode. Le message Git figure juste au-dessus."
        }
        return $GitExitCode
    }

    if (-not $DepuisCsv -and -not (Test-Path Env:BEXIO_API_TOKEN)) {
        throw "BEXIO_API_TOKEN n'est pas definie. Utilisez -DepuisCsv pour publier le CSV existant."
    }

    $Arguments = @(".\update_portal.py")
    if ($DepuisCsv) {
        $Arguments += "--from-csv"
    }

    & python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "La mise a jour du portail a echoue."
    }

    Invoke-ProjectGit -Arguments @("add", "--", "portal/data/projects.json") | Out-Null

    $DiffExitCode = Invoke-ProjectGit `
        -Arguments @("diff", "--cached", "--quiet") `
        -AllowedExitCodes @(0, 1)
    if ($DiffExitCode -eq 0) {
        Write-Host "Aucune modification de dossier a publier."
        exit 0
    }
    if ($DiffExitCode -ne 1) {
        throw "Git n'a pas pu comparer les modifications preparees."
    }

    Invoke-ProjectGit -Arguments @("commit", "-m", $Message) | Out-Null

    Invoke-ProjectGit -Arguments @("push", "origin", "main") | Out-Null

    Write-Host "Mise a jour envoyee. GitHub Pages va republier le portail."
}
finally {
    Pop-Location
}
