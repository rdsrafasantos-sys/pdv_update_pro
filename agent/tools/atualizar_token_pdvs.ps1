#Requires -Version 5.1
<#
.SYNOPSIS
    Atualiza PDV_AGENT_TOKEN nos servicos PDVAgent de um ou mais PDVs.

.DESCRIPTION
    Tres modos de operacao:

    -Local       Atualiza este proprio computador. Execute como Administrador
                 diretamente no PDV.

    -PdvIps      Atualiza remotamente via WinRM. Requer que o WinRM esteja
                 ativo nos PDVs (Enable-PSRemoting -Force no PDV).

    -PdvFile     Igual a -PdvIps, mas le os IPs de um arquivo .txt (um por linha).

    -GerarScript Gera "set_token_local.ps1" para copiar e rodar manualmente
                 em cada PDV sem precisar de WinRM.

.PARAMETER Token
    Novo PDV_AGENT_TOKEN (mesmo valor configurado em PDV_SERVER_TOKEN no servidor).
    Minimo 16 caracteres. Nao pode ser "pdv-agent-2024".

.PARAMETER Local
    Aplica a alteracao neste computador (modo sem WinRM).
    Requer execucao como Administrador.

.PARAMETER PdvIps
    Array de IPs/hostnames dos PDVs para atualizar via WinRM.
    Exemplo: -PdvIps "192.168.1.10","192.168.1.11"

.PARAMETER PdvFile
    Arquivo .txt com um IP de PDV por linha. Linhas com # sao ignoradas.
    Exemplo: -PdvFile ".\pdvs.txt"

.PARAMETER Credential
    Credenciais Windows para autenticar nas maquinas remotas via WinRM.
    Se omitido, usa o usuario atual (funciona em dominios com SSO).

.PARAMETER GerarScript
    Gera "set_token_local.ps1" para execucao manual nos PDVs.

.EXAMPLE
    # Rodar localmente no proprio PDV (como Administrador):
    .\atualizar_token_pdvs.ps1 -Token "385c6ff..." -Local

.EXAMPLE
    # Atualizar via WinRM com lista de IPs:
    .\atualizar_token_pdvs.ps1 -Token "385c6ff..." -PdvIps "192.168.1.10","192.168.1.11"

.EXAMPLE
    # Atualizar via WinRM com arquivo de IPs:
    .\atualizar_token_pdvs.ps1 -Token "385c6ff..." -PdvFile ".\pdvs.txt"

.EXAMPLE
    # Gerar script para copiar e rodar manualmente em cada PDV (sem WinRM):
    .\atualizar_token_pdvs.ps1 -Token "385c6ff..." -GerarScript
#>
param(
    [Parameter(Mandatory, HelpMessage = "Novo PDV_AGENT_TOKEN (min 16 chars)")]
    [string]$Token,

    [switch]$Local,

    [string[]]$PdvIps = @(),

    [string]$PdvFile = "",

    [System.Management.Automation.PSCredential]$Credential,

    [switch]$GerarScript
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Helpers ──────────────────────────────────────────────────────────────────
function Write-Step { param($m) Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Ok   { param($m) Write-Host "    OK   $m" -ForegroundColor Green }
function Write-Warn { param($m) Write-Host "    AVISO $m" -ForegroundColor Yellow }
function Write-Fail { param($m) Write-Host "    ERRO  $m" -ForegroundColor Red }

# ── Validar token ─────────────────────────────────────────────────────────────
if ($Token.Length -lt 16) {
    Write-Host "ERRO: token muito curto ($($Token.Length) chars). Minimo: 16." -ForegroundColor Red
    exit 1
}
if ($Token -eq "pdv-agent-2024") {
    Write-Host "ERRO: nao use o valor padrao inseguro 'pdv-agent-2024'." -ForegroundColor Red
    exit 1
}

# ── Bloco de atualizacao (executa local OU via Invoke-Command) ────────────────
#    Grava PDV_AGENT_TOKEN no registry do servico NSSM e reinicia o PDVAgent.
#    Preserva outras variaveis de ambiente que possam existir no AppEnvironmentExtra.
$blocoAtualizacao = {
    param([string]$NovoToken)

    $svcName  = "PDVAgent"
    $regPath  = "HKLM:\SYSTEM\CurrentControlSet\Services\$svcName\Parameters"
    $propName = "AppEnvironmentExtra"

    # Criar chave Parameters se ainda nao existir (instalacoes antigas podem nao ter)
    if (-not (Test-Path $regPath)) {
        New-Item -Path $regPath -Force | Out-Null
    }

    # Ler valor atual e preservar linhas que NAO sejam PDV_AGENT_TOKEN
    try {
        $atual = (Get-ItemProperty -Path $regPath -Name $propName -ErrorAction SilentlyContinue).$propName
    }
    catch { $atual = @() }

    $linhas  = @($atual) | Where-Object { $_ -and ($_ -notmatch '^PDV_AGENT_TOKEN=') }
    $linhas += "PDV_AGENT_TOKEN=$NovoToken"

    # Gravar como REG_MULTI_SZ
    Set-ItemProperty -Path $regPath -Name $propName -Value $linhas -Type MultiString

    # Reiniciar (ou iniciar) o servico
    $svc = Get-Service -Name $svcName -ErrorAction SilentlyContinue
    if (-not $svc) {
        return "ERRO: servico '$svcName' nao encontrado nesta maquina."
    }

    if ($svc.Status -eq "Running") {
        Restart-Service -Name $svcName -Force -ErrorAction Stop
    }
    else {
        Start-Service -Name $svcName -ErrorAction Stop
    }

    Start-Sleep -Seconds 3
    $status = (Get-Service -Name $svcName -ErrorAction SilentlyContinue).Status
    return "Token atualizado. Servico PDVAgent: $status"
}

# ── Modo -GerarScript ─────────────────────────────────────────────────────────
if ($GerarScript) {
    Write-Step "Gerando script para execucao manual nos PDVs..."

    # Gera um .ps1 autocontido com o token ja embutido
    $conteudo = @"
#Requires -RunAsAdministrator
# set_token_local.ps1 — Execute como Administrador em cada PDV
# Gerado em: $(Get-Date -Format 'yyyy-MM-dd HH:mm')
# Token: $(($Token.Substring(0,6) + "..." + $Token.Substring($Token.Length - 4)))

`$Token    = "$Token"
`$svcName  = "PDVAgent"
`$regPath  = "HKLM:\SYSTEM\CurrentControlSet\Services\`$svcName\Parameters"
`$propName = "AppEnvironmentExtra"

Write-Host "Atualizando PDV_AGENT_TOKEN no PDVAgent..." -ForegroundColor Cyan

if (-not (Test-Path `$regPath)) { New-Item -Path `$regPath -Force | Out-Null }

try { `$atual = (Get-ItemProperty -Path `$regPath -Name `$propName -EA SilentlyContinue).`$propName }
catch { `$atual = @() }

`$linhas  = @(`$atual) | Where-Object { `$_ -and (`$_ -notmatch '^PDV_AGENT_TOKEN=') }
`$linhas += "PDV_AGENT_TOKEN=`$Token"
Set-ItemProperty -Path `$regPath -Name `$propName -Value `$linhas -Type MultiString

`$svc = Get-Service -Name `$svcName -ErrorAction SilentlyContinue
if (-not `$svc) {
    Write-Host "ERRO: servico PDVAgent nao encontrado neste computador." -ForegroundColor Red
    exit 1
}
if (`$svc.Status -eq "Running") { Restart-Service -Name `$svcName -Force }
else { Start-Service -Name `$svcName }

Start-Sleep -Seconds 3
`$status = (Get-Service -Name `$svcName -ErrorAction SilentlyContinue).Status
Write-Host "Concluido! Servico PDVAgent: `$status" -ForegroundColor Green
Write-Host ""
Write-Host "Pressione qualquer tecla para fechar..."
`$null = `$Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
"@

    $saida = Join-Path $PSScriptRoot "set_token_local.ps1"
    [System.IO.File]::WriteAllText($saida, $conteudo, [System.Text.UTF8Encoding]::new($false))
    Write-Ok "Script gerado: $saida"
    Write-Host ""
    Write-Host "  Instrucoes de uso:" -ForegroundColor White
    Write-Host "  1. Copie o arquivo set_token_local.ps1 para cada PDV (pendrive, compartilhamento, etc.)" -ForegroundColor White
    Write-Host "  2. No PDV, clique com botao direito no arquivo > Executar com PowerShell" -ForegroundColor White
    Write-Host "     (ou: powershell -ExecutionPolicy Bypass -File set_token_local.ps1)" -ForegroundColor Yellow
    Write-Host ""
    exit 0
}

# ── Modo -Local ───────────────────────────────────────────────────────────────
if ($Local) {
    $ehAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $ehAdmin) {
        Write-Host "ERRO: execute como Administrador!" -ForegroundColor Red
        exit 1
    }

    Write-Step "Atualizando token neste computador ($env:COMPUTERNAME)..."
    try {
        $resultado = & $blocoAtualizacao $Token
        if ($resultado -like "ERRO*") { Write-Fail $resultado; exit 1 }
        Write-Ok $resultado
    }
    catch {
        Write-Fail $_.Exception.Message; exit 1
    }
    exit 0
}

# ── Montar lista de IPs ────────────────────────────────────────────────────────
$listaIps = [System.Collections.Generic.List[string]]::new()

foreach ($ip in $PdvIps) {
    $t = $ip.Trim()
    if ($t) { $listaIps.Add($t) }
}

if ($PdvFile) {
    if (-not (Test-Path $PdvFile)) {
        Write-Host "ERRO: arquivo '$PdvFile' nao encontrado." -ForegroundColor Red
        exit 1
    }
    foreach ($linha in [System.IO.File]::ReadAllLines($PdvFile)) {
        $t = $linha.Trim()
        if ($t -and -not $t.StartsWith('#')) { $listaIps.Add($t) }
    }
}

# ── Modo WinRM ────────────────────────────────────────────────────────────────
if ($listaIps.Count -gt 0) {
    Write-Host ""
    Write-Host "PDVs a atualizar: $($listaIps.Count)" -ForegroundColor Cyan
    $resultados = [ordered]@{}

    foreach ($ip in $listaIps) {
        Write-Step $ip
        try {
            $invokeParams = @{
                ComputerName = $ip
                ScriptBlock  = $blocoAtualizacao
                ArgumentList = $Token
                ErrorAction  = "Stop"
            }
            if ($Credential) { $invokeParams["Credential"] = $Credential }

            $r = Invoke-Command @invokeParams
            if ($r -like "ERRO*") {
                Write-Fail $r
                $resultados[$ip] = "ERRO: $r"
            }
            else {
                Write-Ok $r
                $resultados[$ip] = "OK"
            }
        }
        catch {
            $msg = $_.Exception.Message
            Write-Fail "WinRM falhou: $msg"
            Write-Warn "Alternativa: gere o script manual e execute no PDV:"
            Write-Host "    .\atualizar_token_pdvs.ps1 -Token `"$Token`" -GerarScript" -ForegroundColor Yellow
            $resultados[$ip] = "FALHOU (WinRM): $msg"
        }
    }

    # Resumo
    $ok    = ($resultados.Values | Where-Object { $_ -eq "OK" }).Count
    $falha = $listaIps.Count - $ok
    Write-Host ""
    Write-Host "══════════════════════ RESUMO ══════════════════════" -ForegroundColor Cyan
    foreach ($ip in $listaIps) {
        $s = $resultados[$ip]
        if ($s -eq "OK") { Write-Host "  [OK]   $ip" -ForegroundColor Green }
        else              { Write-Host "  [ERRO] $ip — $s" -ForegroundColor Red }
    }
    Write-Host "    Sucesso: $ok / $($listaIps.Count)   Falha: $falha" -ForegroundColor Cyan
    Write-Host ""

    if ($falha -gt 0) {
        Write-Host "Para os PDVs que falharam, use o modo -GerarScript:" -ForegroundColor Yellow
        Write-Host "  .\atualizar_token_pdvs.ps1 -Token `"$Token`" -GerarScript" -ForegroundColor Yellow
        Write-Host ""
    }
    exit ($falha -gt 0 ? 1 : 0)
}

# ── Nenhum modo selecionado — exibir ajuda rapida ──────────────────────────────
Write-Host ""
Write-Host "Nenhum modo selecionado. Exemplos de uso:" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Localmente no proprio PDV (rodar como Administrador no PDV):" -ForegroundColor White
Write-Host "    .\atualizar_token_pdvs.ps1 -Token `"$Token`" -Local" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Via WinRM (da maquina admin, para multiplos PDVs):" -ForegroundColor White
Write-Host "    .\atualizar_token_pdvs.ps1 -Token `"$Token`" -PdvIps `"192.168.1.10`",`"192.168.1.11`"" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Via WinRM lendo IPs de um arquivo:" -ForegroundColor White
Write-Host "    .\atualizar_token_pdvs.ps1 -Token `"$Token`" -PdvFile `".\pdvs.txt`"" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Gerar script .ps1 para copiar e rodar manualmente (sem WinRM):" -ForegroundColor White
Write-Host "    .\atualizar_token_pdvs.ps1 -Token `"$Token`" -GerarScript" -ForegroundColor Cyan
Write-Host ""
