; ===============================================================
;  PDVAgent_Setup.nsi
;  Instalador do PDV Agent
;  Compilar com: makensis PDVAgent_Setup.nsi
;  Requer na mesma pasta (copie do dist/ gerado pelo PyInstaller):
;    - agente.exe
;    - status_pdv.exe
;    - nssm.exe
;    - tailscale-setup-amd64.msi (baixe em https://pkgs.tailscale.com/stable/#windows)
; ===============================================================

Unicode true

!include "MUI2.nsh"
!include "nsDialogs.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"

;--------------------------------
; Configurações gerais
;--------------------------------
!define PRODUTO        "PDV Agent"
!define VERSAO         "1.4.6"
!define FABRICANTE     "VR Software"
!define PASTA_DESTINO  "C:\PDVAgent"
!define NOME_SERVICO   "PDVAgent"
!define EXE_AGENTE     "agente.exe"
!define EXE_STATUS     "status_pdv.exe"
!define EXE_NSSM       "nssm.exe"
!define MSI_TAILSCALE  "tailscale-setup-amd64.msi"
!define PORTA          "5000"

Var AgentToken
Var TailscaleAuthKey
Var TailscaleHostname
Var hCtlAgentToken
Var hCtlTailscaleAuthKey
Var hCtlTailscaleHostname
Var hCtlImportBtn

Name "${PRODUTO} ${VERSAO}"
OutFile "PDVAgent_Setup.exe"
InstallDir "${PASTA_DESTINO}"
RequestExecutionLevel admin
ShowInstDetails show
SetCompressor /SOLID lzma

;--------------------------------
; Interface moderna
;--------------------------------
!define MUI_ABORTWARNING
!define MUI_ICON           "${NSISDIR}\Contrib\Graphics\Icons\modern-install.ico"
!define MUI_UNICON         "${NSISDIR}\Contrib\Graphics\Icons\modern-uninstall.ico"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_BITMAP "${NSISDIR}\Contrib\Graphics\Header\win.bmp"
!define MUI_WELCOMEFINISHPAGE_BITMAP "${NSISDIR}\Contrib\Graphics\Wizard\win.bmp"

; Cor de destaque laranja VR
!define MUI_COLOR "E8530A"

; Telas do wizard
!define MUI_WELCOMEPAGE_TITLE "Bem-vindo ao instalador do ${PRODUTO}"
!define MUI_WELCOMEPAGE_TEXT "Este assistente vai instalar o ${PRODUTO} ${VERSAO} neste computador.$\r$\n$\r$\nO agente permite que este PDV receba atualizações remotas do servidor.$\r$\n$\r$\nClique em Avançar para continuar."

!define MUI_FINISHPAGE_TITLE "Instalação concluída!"
!define MUI_FINISHPAGE_TEXT "O ${PRODUTO} foi instalado com sucesso.$\r$\n$\r$\nO serviço está rodando e o PDV está pronto para receber atualizações remotas.$\r$\n$\r$\nPorta: ${PORTA}"
!define MUI_FINISHPAGE_NOAUTOCLOSE

!insertmacro MUI_PAGE_WELCOME
Page custom TailscalePageCreate TailscalePageLeave
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "PortugueseBR"

;--------------------------------
; Modo silencioso: ler parametros da linha de comando
; Uso: PDVAgent_Setup.exe /S /TOKEN=xxx [/AUTHKEY=yyy] [/HOSTNAME=zzz]
;--------------------------------
Function .onInit
  ${If} ${Silent}
    ${GetOptions} $CMDLINE "/TOKEN=" $AgentToken
    ${If} $AgentToken == ""
      SetErrorLevel 1   ; TOKEN ausente
      Quit
    ${EndIf}
    StrLen $R0 $AgentToken
    ${If} $R0 < 16
      SetErrorLevel 2   ; TOKEN muito curto
      Quit
    ${EndIf}
    ${If} $AgentToken == "pdv-agent-2024"
      SetErrorLevel 3   ; TOKEN padrao inseguro
      Quit
    ${EndIf}
    ${GetOptions} $CMDLINE "/AUTHKEY=" $TailscaleAuthKey
    ${GetOptions} $CMDLINE "/HOSTNAME=" $TailscaleHostname
  ${EndIf}
FunctionEnd

;--------------------------------
; Pagina customizada: Tailscale (opcional)
;--------------------------------
Function OnImportConfig
  ; Abre seletor de arquivo e preenche os campos a partir do pdv_config.ini
  nsDialogs::SelectFileDialog open "" "Config PDV (*.ini)|*.ini|Todos os arquivos (*.*)|*.*"
  Pop $R0
  ${If} $R0 != ""
    ReadINIStr $R1 "$R0" "PDVAgent" "TOKEN"
    ${If} $R1 != ""
      ${NSD_SetText} $hCtlAgentToken $R1
    ${EndIf}
    ReadINIStr $R2 "$R0" "PDVAgent" "AUTHKEY"
    ${NSD_SetText} $hCtlTailscaleAuthKey $R2
    ReadINIStr $R3 "$R0" "PDVAgent" "HOSTNAME"
    ${NSD_SetText} $hCtlTailscaleHostname $R3
  ${EndIf}
FunctionEnd

Function TailscalePageCreate
  nsDialogs::Create 1018
  Pop $0

  ; ── Importar arquivo de configuração ──────────────────────────────────────
  ${NSD_CreateLabel} 0 0u 100% 9u "Importar configuracao do painel:"
  Pop $0
  ${NSD_CreateButton} 0 10u 100% 14u "Importar pdv_config.ini baixado do painel..."
  Pop $hCtlImportBtn
  ${NSD_OnClick} $hCtlImportBtn OnImportConfig

  ${NSD_CreateLabel} 0 27u 100% 8u "────────────────── ou preencha manualmente ──────────────────"
  Pop $0

  ; ── Token de acesso (OBRIGATORIO) ────────────────────────────────────────
  ${NSD_CreateLabel} 0 38u 100% 9u "Token de Acesso ao Servidor *"
  Pop $0
  ${NSD_CreateText} 0 48u 100% 12u ""
  Pop $hCtlAgentToken

  ${NSD_CreateLabel} 0 62u 100% 9u "Obrigatorio. Baixe o arquivo acima ou obtenha com o administrador."
  Pop $0

  ; ── Tailscale (OPCIONAL) ──────────────────────────────────────────────────
  ${NSD_CreateLabel} 0 74u 100% 9u "Auth Key Tailscale (opcional):"
  Pop $0
  ${NSD_CreateText} 0 84u 100% 12u ""
  Pop $hCtlTailscaleAuthKey

  ${NSD_CreateLabel} 0 98u 100% 9u "Nome deste PDV na rede Tailscale (opcional, ex: loja01-pdv03):"
  Pop $0
  ${NSD_CreateText} 0 108u 100% 12u ""
  Pop $hCtlTailscaleHostname

  ${NSD_CreateLabel} 0 122u 100% 9u "Deixe os campos opcionais em branco para configurar depois."
  Pop $0

  nsDialogs::Show
FunctionEnd

Function TailscalePageLeave
  ${NSD_GetText} $hCtlAgentToken $AgentToken
  ${NSD_GetText} $hCtlTailscaleAuthKey $TailscaleAuthKey
  ${NSD_GetText} $hCtlTailscaleHostname $TailscaleHostname

  ; Validar token: nao pode ser vazio
  ${If} $AgentToken == ""
    MessageBox MB_ICONEXCLAMATION "O Token de Acesso ao Servidor e obrigatorio.$\r$\nObtena com o administrador do sistema."
    Abort
  ${EndIf}

  ; Validar token: minimo 16 caracteres
  StrLen $R0 $AgentToken
  ${If} $R0 < 16
    MessageBox MB_ICONEXCLAMATION "O Token de Acesso deve ter pelo menos 16 caracteres.$\r$\nVerifique com o administrador."
    Abort
  ${EndIf}

  ; Validar token: nao pode ser o valor padrao inseguro
  ${If} $AgentToken == "pdv-agent-2024"
    MessageBox MB_ICONEXCLAMATION "Token invalido: nao use o valor padrao 'pdv-agent-2024'.$\r$\nObtena o token correto com o administrador."
    Abort
  ${EndIf}
FunctionEnd

;--------------------------------
; Seção principal de instalação
;--------------------------------
Section "PDV Agent" SecPrincipal

  SetOutPath "${PASTA_DESTINO}"

  ; ── 1. Copia os arquivos ──────────────────────
  DetailPrint "Copiando arquivos..."

  ; Para o serviço se já existir
  nsExec::ExecToLog '"${PASTA_DESTINO}\${EXE_NSSM}" stop ${NOME_SERVICO}'
  Sleep 2000
  nsExec::ExecToLog 'sc delete ${NOME_SERVICO}'
  Sleep 1000

  File "${EXE_AGENTE}"
  File "${EXE_STATUS}"
  File "${EXE_NSSM}"

  DetailPrint "Arquivos copiados com sucesso."

  ; ── 2. Instala o serviço via NSSM ─────────────
  DetailPrint "Instalando serviço Windows..."

  nsExec::ExecToLog '"${PASTA_DESTINO}\${EXE_NSSM}" install ${NOME_SERVICO} "${PASTA_DESTINO}\${EXE_AGENTE}"'
  nsExec::ExecToLog '"${PASTA_DESTINO}\${EXE_NSSM}" set ${NOME_SERVICO} DisplayName "PDV Agent - Atualizador Remoto"'
  nsExec::ExecToLog '"${PASTA_DESTINO}\${EXE_NSSM}" set ${NOME_SERVICO} Description "Agente de atualizacao remota do PDV. Nao desative este servico."'
  nsExec::ExecToLog '"${PASTA_DESTINO}\${EXE_NSSM}" set ${NOME_SERVICO} Start SERVICE_AUTO_START'
  nsExec::ExecToLog '"${PASTA_DESTINO}\${EXE_NSSM}" set ${NOME_SERVICO} AppStdout "${PASTA_DESTINO}\agente_pdv.log"'
  nsExec::ExecToLog '"${PASTA_DESTINO}\${EXE_NSSM}" set ${NOME_SERVICO} AppStderr "${PASTA_DESTINO}\agente_pdv.log"'

  ; Configurar token de seguranca ANTES de iniciar o servico
  DetailPrint "Configurando token de acesso..."
  nsExec::ExecToLog '"${PASTA_DESTINO}\${EXE_NSSM}" set ${NOME_SERVICO} AppEnvironmentExtra "PDV_AGENT_TOKEN=$AgentToken"'

  DetailPrint "Serviço instalado."

  ; ── 3. Inicia o serviço ───────────────────────
  DetailPrint "Iniciando serviço PDVAgent..."
  nsExec::ExecToLog '"${PASTA_DESTINO}\${EXE_NSSM}" start ${NOME_SERVICO}'
  Sleep 2000

  DetailPrint "Serviço iniciado."

  ; ── 4. Firewall ───────────────────────────────
  ; profile=domain,private impede exposicao em redes publicas (Wi-Fi de eventos etc.)
  DetailPrint "Configurando firewall (porta ${PORTA})..."
  nsExec::ExecToLog 'netsh advfirewall firewall delete rule name="PDV Agent"'
  nsExec::ExecToLog 'netsh advfirewall firewall add rule name="PDV Agent" dir=in action=allow protocol=TCP localport=${PORTA} profile=domain,private'

  DetailPrint "Firewall configurado."

  ; ── 4b. Tailscale (opcional) ───────────────────
  ${If} $TailscaleAuthKey != ""
    IfFileExists "$PROGRAMFILES64\Tailscale\tailscale.exe" TailscaleJaInstalado TailscaleInstalar

    TailscaleInstalar:
      DetailPrint "Instalando Tailscale..."
      File "${MSI_TAILSCALE}"
      ExecWait 'msiexec /i "${PASTA_DESTINO}\${MSI_TAILSCALE}" TS_NOLAUNCH=1 TS_UNATTENDEDMODE=always /quiet /norestart' $0
      Delete "${PASTA_DESTINO}\${MSI_TAILSCALE}"
      DetailPrint "Tailscale instalado (codigo: $0)."
      Goto TailscaleConectar

    TailscaleJaInstalado:
      DetailPrint "Tailscale ja esta instalado neste PDV — pulando instalacao."

    TailscaleConectar:
      DetailPrint "Conectando este PDV a rede Tailscale..."
      ${If} $TailscaleHostname != ""
        StrCpy $1 ' --hostname=$TailscaleHostname'
      ${Else}
        StrCpy $1 ''
      ${EndIf}
      ; nsExec::Exec (sem log) para nao expor a auth key na tela/log do instalador
      nsExec::Exec '"$PROGRAMFILES64\Tailscale\tailscale.exe" up --auth-key=$TailscaleAuthKey --unattended$1'
      Pop $0
      ${If} $0 == 0
        DetailPrint "Tailscale conectado com sucesso."
      ${Else}
        DetailPrint "AVISO: falha ao conectar ao Tailscale (codigo $0). Verifique a auth key e, se necessario, rode manualmente depois: tailscale up --auth-key=<key> --unattended"
      ${EndIf}
  ${Else}
    DetailPrint "Auth key do Tailscale nao informada — pulando configuracao de VPN."
  ${EndIf}

  ; ── 5. status_pdv.exe na inicialização ────────
  DetailPrint "Configurando inicialização automática..."
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" \
              "PDVStatus" "${PASTA_DESTINO}\${EXE_STATUS}"

  DetailPrint "Inicialização configurada."

  ; ── 6. Inicia o status_pdv.exe agora ──────────
  Exec '"${PASTA_DESTINO}\${EXE_STATUS}"'

  ; ── 7. Desinstalador ──────────────────────────
  WriteUninstaller "${PASTA_DESTINO}\Desinstalar.exe"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${NOME_SERVICO}" \
              "DisplayName" "${PRODUTO} ${VERSAO}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${NOME_SERVICO}" \
              "UninstallString" "${PASTA_DESTINO}\Desinstalar.exe"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${NOME_SERVICO}" \
              "Publisher" "${FABRICANTE}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${NOME_SERVICO}" \
              "DisplayVersion" "${VERSAO}"

  DetailPrint ""
  DetailPrint "=============================="
  DetailPrint "PDV Agent instalado com sucesso!"
  DetailPrint "Porta: ${PORTA}"
  DetailPrint "Log: ${PASTA_DESTINO}\agente_pdv.log"
  DetailPrint "=============================="

SectionEnd

;--------------------------------
; Desinstalador
;--------------------------------
Section "Uninstall"

  DetailPrint "Parando e removendo serviço..."
  nsExec::ExecToLog '"${PASTA_DESTINO}\${EXE_NSSM}" stop ${NOME_SERVICO}'
  Sleep 2000
  nsExec::ExecToLog '"${PASTA_DESTINO}\${EXE_NSSM}" remove ${NOME_SERVICO} confirm'
  Sleep 1000

  DetailPrint "Removendo regras de firewall..."
  nsExec::ExecToLog 'netsh advfirewall firewall delete rule name="PDV Agent"'

  DetailPrint "Removendo inicialização automática..."
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "PDVStatus"

  DetailPrint "Removendo arquivos..."
  Delete "${PASTA_DESTINO}\${EXE_AGENTE}"
  Delete "${PASTA_DESTINO}\${EXE_STATUS}"
  Delete "${PASTA_DESTINO}\${EXE_NSSM}"
  Delete "${PASTA_DESTINO}\agente_pdv.log"
  Delete "${PASTA_DESTINO}\progresso.json"
  Delete "${PASTA_DESTINO}\Desinstalar.exe"
  RMDir  "${PASTA_DESTINO}"

  DetailPrint "Removendo entradas do registro..."
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${NOME_SERVICO}"

  DetailPrint "PDV Agent removido com sucesso."

SectionEnd
