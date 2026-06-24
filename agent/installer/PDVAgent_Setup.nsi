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

Var TailscaleAuthKey
Var TailscaleHostname
Var hCtlTailscaleAuthKey
Var hCtlTailscaleHostname

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
; Pagina customizada: Tailscale (opcional)
;--------------------------------
Function TailscalePageCreate
  nsDialogs::Create 1018
  Pop $0

  ${NSD_CreateLabel} 0 0u 100% 30u "Tailscale (opcional): cole abaixo a auth key da rede deste cliente para conectar este PDV automaticamente a VPN. Deixe em branco para pular esta etapa (pode ser configurado depois)."
  Pop $0

  ${NSD_CreateLabel} 0 36u 100% 10u "Auth Key:"
  Pop $0
  ${NSD_CreateText} 0 47u 100% 12u ""
  Pop $hCtlTailscaleAuthKey

  ${NSD_CreateLabel} 0 64u 100% 10u "Nome deste PDV na rede Tailscale (opcional, ex: bonna-loja01-pdv03):"
  Pop $0
  ${NSD_CreateText} 0 75u 100% 12u ""
  Pop $hCtlTailscaleHostname

  nsDialogs::Show
FunctionEnd

Function TailscalePageLeave
  ${NSD_GetText} $hCtlTailscaleAuthKey $TailscaleAuthKey
  ${NSD_GetText} $hCtlTailscaleHostname $TailscaleHostname
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

  DetailPrint "Serviço instalado."

  ; ── 3. Inicia o serviço ───────────────────────
  DetailPrint "Iniciando serviço PDVAgent..."
  nsExec::ExecToLog '"${PASTA_DESTINO}\${EXE_NSSM}" start ${NOME_SERVICO}'
  Sleep 2000

  DetailPrint "Serviço iniciado."

  ; ── 4. Firewall ───────────────────────────────
  DetailPrint "Configurando firewall (porta ${PORTA})..."
  nsExec::ExecToLog 'netsh advfirewall firewall delete rule name="PDV Agent"'
  nsExec::ExecToLog 'netsh advfirewall firewall add rule name="PDV Agent" dir=in action=allow protocol=TCP localport=${PORTA}'

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
