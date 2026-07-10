#Requires -Version 5.1
<#
.SYNOPSIS
    Compila agente.exe + status_pdv.exe, empacota no PDVAgent_Setup.exe
    e faz upload automatico para os servidores configurados.

.DESCRIPTION
    Fluxo completo:
      1. PyInstaller  -> dist/agente.exe + dist/status_pdv.exe
      2. Copia binarios para installer/
      3. makensis     -> installer/PDVAgent_Setup.exe
      4. Upload para staging e/ou prod via API

.PARAMETER Staging
    Faz upload para o servidor de staging (192.168.1.126:8889). Padrao: $true

.PARAMETER Prod
    Faz upload para producao (pdvproupdater.com.br). Padrao: $false
    Requer confirmacao explicita.

.PARAMETER Token
    Token de autenticacao para upload. Se omitido, le de $env:PDV_SETUP_UPLOAD_TOKEN.

.EXAMPLE
    # Compilar e subir so no staging (fluxo normal de dev):
    .\build_and_deploy.ps1

    # Compilar e subir em staging E prod:
    .\build_and_deploy.ps1 -Prod

    # Subir sem recompilar (so o deploy):
    .\build_and_deploy.ps1 -SkipBuild
#>
param(
    [switch]$Staging = $true,
    [switch]$Prod,
    [switch]$SkipBuild,
    [string]$Token = $env:PDV_SETUP_UPLOAD_TOKEN
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$AgentDir    = $PSScriptRoot
$InstallerDir = Join-Path $AgentDir "installer"
$SetupExe    = Join-Path $InstallerDir "PDVAgent_Setup.exe"

# ── Cores no terminal ──────────────────────────────────────────
function Write-Step  { param($msg) Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok    { param($msg) Write-Host "    OK  $msg" -ForegroundColor Green }
function Write-Fail  { param($msg) Write-Host "    ERRO $msg" -ForegroundColor Red; exit 1 }

# ── 1. Compilar ────────────────────────────────────────────────
if (-not $SkipBuild) {
    Write-Step "PyInstaller — compilando agente.exe e status_pdv.exe"
    Push-Location $AgentDir
    python -m PyInstaller build_agent.spec --noconfirm
    if ($LASTEXITCODE -ne 0) { Write-Fail "PyInstaller falhou" }
    Pop-Location
    Write-Ok "Binarios gerados em dist/"

    Write-Step "Copiando binarios para $InstallerDir"
    Copy-Item (Join-Path $AgentDir "dist\agente.exe")    $InstallerDir -Force
    Copy-Item (Join-Path $AgentDir "dist\status_pdv.exe") $InstallerDir -Force
    Write-Ok "agente.exe e status_pdv.exe copiados"

    # Verificar dependencias obrigatorias
    $deps = @("nssm.exe", "tailscale-setup-amd64.msi")
    foreach ($dep in $deps) {
        if (-not (Test-Path (Join-Path $InstallerDir $dep))) {
            Write-Fail "$dep nao encontrado em $InstallerDir — copie antes de buildar"
        }
    }

    Write-Step "makensis — gerando PDVAgent_Setup.exe"
    Push-Location $InstallerDir
    makensis PDVAgent_Setup.nsi
    if ($LASTEXITCODE -ne 0) { Write-Fail "makensis falhou" }
    Pop-Location
    Write-Ok "PDVAgent_Setup.exe gerado em $InstallerDir"
} else {
    Write-Step "SkipBuild ativo — usando Setup existente em $SetupExe"
    if (-not (Test-Path $SetupExe)) { Write-Fail "PDVAgent_Setup.exe nao encontrado. Execute sem -SkipBuild primeiro." }
}

# ── 2. Validar token ───────────────────────────────────────────
if (-not $Token) {
    Write-Fail "Token de upload nao definido. Defina `$env:PDV_SETUP_UPLOAD_TOKEN ou passe -Token <valor>"
}

# ── 3. Funcao de upload ────────────────────────────────────────
function Upload-Setup {
    param([string]$BaseUrl, [string]$Label)
    Write-Step "Upload para $Label ($BaseUrl)"
    try {
        $form = @{ arquivo = Get-Item $SetupExe }
        $resp = Invoke-RestMethod `
            -Uri "$BaseUrl/api/setup/upload" `
            -Method Post `
            -Headers @{ Authorization = "Bearer $Token" } `
            -Form $form
        Write-Ok "$Label — $($resp.tamanho_mb) MB enviado em $($resp.data)"
    } catch {
        Write-Fail "Upload para $Label falhou: $_"
    }
}

# ── 4. Deploy ──────────────────────────────────────────────────
if ($Staging) {
    Upload-Setup -BaseUrl "http://192.168.1.126:8889" -Label "Staging"
}

if ($Prod) {
    Write-Host "`n[ATENCAO] Voce esta prestes a enviar o setup para PRODUCAO (pdvproupdater.com.br)." -ForegroundColor Yellow
    $conf = Read-Host "Confirme digitando 'prod'"
    if ($conf -ne "prod") { Write-Host "Cancelado." -ForegroundColor Yellow; exit 0 }
    Upload-Setup -BaseUrl "https://pdvproupdater.com.br" -Label "Prod"
}

Write-Host "`nConcluido!" -ForegroundColor Green
