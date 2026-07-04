# ===============================================================
#  instalar_servico.ps1
#  Instala o PDV Agent como serviço Windows usando NSSM
#  Execute como Administrador no PDV
# ===============================================================

Write-Host "=== Instalando PDV Agent como Servico Windows ===" -ForegroundColor Cyan

$NomeServico = "PDVAgent"
$PastaAgente = "C:\PDVAgent"
$ExeAgente   = "C:\PDVAgent\agente.exe"
$NssmExe     = "C:\PDVAgent\nssm.exe"
$PastaScript = $PSScriptRoot

# 0. Verificar Administrador
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "ERRO: Execute como Administrador!" -ForegroundColor Red
    exit 1
}

# 1. Criar pasta
Write-Host "[1/6] Criando pasta $PastaAgente..." -ForegroundColor Yellow
New-Item -ItemType Directory -Path $PastaAgente -Force | Out-Null
Write-Host "Pasta pronta." -ForegroundColor Green

# 2. Copiar agente.exe
Write-Host "[2/6] Copiando agente.exe..." -ForegroundColor Yellow
$origem = Join-Path $PastaScript "agente.exe"
if (-not (Test-Path $origem)) {
    Write-Host "ERRO: agente.exe nao encontrado em $origem" -ForegroundColor Red
    exit 1
}
Copy-Item -Path $origem -Destination $ExeAgente -Force
Write-Host "agente.exe copiado." -ForegroundColor Green

# 3. Instalar NSSM (gerenciador de servicos)
Write-Host "[3/6] Configurando NSSM..." -ForegroundColor Yellow

# Verifica se NSSM já existe no destino
if (Test-Path $NssmExe) {
    Write-Host "NSSM ja existe em $NssmExe." -ForegroundColor Green
} else {
    # Verifica se nssm.exe está na mesma pasta do script
    $nssmLocal = Join-Path $PastaScript "nssm.exe"
    if (Test-Path $nssmLocal) {
        Copy-Item -Path $nssmLocal -Destination $NssmExe -Force
        Write-Host "NSSM copiado da pasta local." -ForegroundColor Green
    } else {
    $urls = @(
        "https://nssm.cc/release/nssm-2.24.zip",
        "https://github.com/nickelc/nssm/releases/download/2.24/nssm-2.24.zip",
        "https://www.nssm.cc/release/nssm-2.24.zip"
    )
    $baixou = $false
    foreach ($url in $urls) {
        try {
            Write-Host "Tentando: $url" -ForegroundColor Yellow
            $zip  = "$env:TEMP\nssm.zip"
            $dest = "$env:TEMP\nssm"
            Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing -TimeoutSec 15
            Expand-Archive -Path $zip -DestinationPath $dest -Force
            $nssmBin = Get-ChildItem -Path $dest -Recurse -Filter "nssm.exe" |
                       Where-Object { $_.FullName -like "*win64*" } |
                       Select-Object -First 1
            if ($nssmBin) {
                Copy-Item -Path $nssmBin.FullName -Destination $NssmExe -Force
                Write-Host "NSSM instalado." -ForegroundColor Green
                $baixou = $true
                break
            }
        } catch {
            Write-Host "Falhou: $_" -ForegroundColor Yellow
        }
    }
    if (-not $baixou) {
            Write-Host "ERRO: NSSM nao encontrado." -ForegroundColor Red
            Write-Host "Coloque o nssm.exe na mesma pasta que este script e tente novamente." -ForegroundColor Yellow
            exit 1
        }
    }
}

# 4. Remover serviço anterior se existir
Write-Host "[4/6] Verificando servico anterior..." -ForegroundColor Yellow
$svcExistente = Get-Service -Name $NomeServico -ErrorAction SilentlyContinue
if ($svcExistente) {
    Write-Host "Removendo servico anterior..." -ForegroundColor Yellow
    if ($svcExistente.Status -eq "Running") {
        & $NssmExe stop $NomeServico | Out-Null
        Start-Sleep -Seconds 2
    }
    & $NssmExe remove $NomeServico confirm | Out-Null
    Start-Sleep -Seconds 1
    Write-Host "Servico anterior removido." -ForegroundColor Green
} else {
    Write-Host "Nenhum servico anterior." -ForegroundColor Green
}

# 5. Instalar e iniciar o serviço via NSSM
Write-Host "[5/6] Instalando servico com NSSM..." -ForegroundColor Yellow
& $NssmExe install $NomeServico $ExeAgente
& $NssmExe set $NomeServico DisplayName "PDV Agent - Atualizador Remoto"
& $NssmExe set $NomeServico Description "Agente de atualizacao remota do PDV. Nao desative."
& $NssmExe set $NomeServico Start SERVICE_AUTO_START
& $NssmExe set $NomeServico AppStdout "C:\PDVAgent\agente_pdv.log"
& $NssmExe set $NomeServico AppStderr "C:\PDVAgent\agente_pdv.log"
& $NssmExe start $NomeServico
Start-Sleep -Seconds 3

# 6. Abrir porta 5000 no Firewall
Write-Host "[6/7] Abrindo porta 5000 no Firewall..." -ForegroundColor Yellow
$regra = Get-NetFirewallRule -DisplayName "PDV Agent" -ErrorAction SilentlyContinue
if (-not $regra) {
    New-NetFirewallRule `
        -DisplayName "PDV Agent" `
        -Direction Inbound `
        -Protocol TCP `
        -LocalPort 5000 `
        -Action Allow `
        -Profile Any | Out-Null
    Write-Host "Regra de firewall criada." -ForegroundColor Green
} else {
    Write-Host "Regra de firewall ja existe." -ForegroundColor Green
}

# Resultado
$status = (Get-Service -Name $NomeServico -ErrorAction SilentlyContinue).Status
if ($status -eq "Running") {
    Write-Host ""
    Write-Host "=== PDV Agent instalado com sucesso! ===" -ForegroundColor Green
    Write-Host "Servico : $NomeServico" -ForegroundColor Green
    Write-Host "Status  : $status" -ForegroundColor Green
    Write-Host "Porta   : 5000" -ForegroundColor Green
    Write-Host "Log     : C:\PDVAgent\agente_pdv.log" -ForegroundColor Green
} else {
    Write-Host "ERRO: Servico nao iniciou. Status: $status" -ForegroundColor Red
    Write-Host "Verifique: C:\PDVAgent\agente_pdv.log" -ForegroundColor Yellow
}

Write-Host ""
# 7. Configurar status_pdv.exe via Task Scheduler (mais confiavel que Run key no W11)
Write-Host "[7/7] Configurando status_pdv.exe na inicializacao..." -ForegroundColor Yellow
$statusExe = "C:\PDVAgent\status_pdv.exe"
$origem2   = Join-Path $PastaScript "status_pdv.exe"
if (Test-Path $origem2) {
    Copy-Item -Path $origem2 -Destination $statusExe -Force

    # Remove Run key legada (se existir)
    $regKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    Remove-ItemProperty -Path $regKey -Name "PDVStatus" -ErrorAction SilentlyContinue

    # Task Scheduler: roda ao login de qualquer usuario, interactive
    $action  = New-ScheduledTaskAction -Execute $statusExe
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0 -RestartCount 3 `
                    -RestartInterval (New-TimeSpan -Minutes 1)
    # Sem -Principal: usa o usuario atual (funciona em qualquer idioma do Windows)
    Register-ScheduledTask -TaskName "PDVStatus" -Action $action -Trigger $trigger `
        -Settings $settings -Force | Out-Null

    # Inicia imediatamente sem precisar de logout/login
    Stop-Process -Name "status_pdv" -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    Start-Process -FilePath $statusExe
    Write-Host "status_pdv.exe configurado no Task Scheduler e iniciado agora." -ForegroundColor Green
} else {
    Write-Host "AVISO: status_pdv.exe nao encontrado em $origem2" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Pressione qualquer tecla para sair..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
