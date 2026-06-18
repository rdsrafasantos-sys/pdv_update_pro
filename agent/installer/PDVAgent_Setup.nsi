; ===============================================================
;  PDVAgent_Setup.nsi
;  Instalador do PDV Agent
;  Compilar com: makensis PDVAgent_Setup.nsi
;  Requer na mesma pasta (copie do dist/ gerado pelo PyInstaller):
;    - agente.exe
;    - status_pdv.exe
;    - nssm.exe
; ===============================================================

;--------------------------------
; Configurações gerais
;--------------------------------
!define PRODUTO        "PDV Agent"
!define VERSAO         "1.4.5"
!define FABRICANTE     "VR Software"
!define PASTA_DESTINO  "C:\PDVAgent"
!define NOME_SERVICO   "PDVAgent"
!define EXE_AGENTE     "agente.exe"
!define EXE_STATUS     "status_pdv.exe"
!define EXE_NSSM       "nssm.exe"
!define PORTA          "5000"

Name "${PRODUTO} ${VERSAO}"
OutFile "PDVAgent_Setup.exe"
InstallDir "${PASTA_DESTINO}"
RequestExecutionLevel admin
ShowInstDetails show
SetCompressor /SOLID lzma

;--------------------------------
; Interface moderna
;--------------------------------
!include "MUI2.nsh"

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
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "PortugueseBR"

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
