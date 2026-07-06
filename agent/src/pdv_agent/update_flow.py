import json
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
import zipfile

from pdv_agent.config import (
    DB_DIR, DB_TEMP_DIR, PROCESSOS, PROGRESSO_FILE, TEMP_ZIP,
    VRPDV_DIR, VRPDV_OLD_DIR,
)
from pdv_agent.lmdb_reader import invalidar_cache_info_pdv
from pdv_agent.service_control import (
    detectar_servicos, get_status_servico, processo_rodando,
)

log = logging.getLogger("pdv_agent")

estado = {
    "status": "idle", "etapa": "", "progresso": 0,
    "mensagem": "", "erro": "", "inicio": None, "fim": None
}
lock = threading.Lock()


def set_estado(status, etapa, progresso, mensagem="", erro=""):
    with lock:
        estado.update({"status": status, "etapa": etapa, "progresso": progresso,
                        "mensagem": mensagem, "erro": erro})
        if status == "updating" and progresso == 0:
            estado["inicio"] = time.strftime("%Y-%m-%d %H:%M:%S")
            estado["fim"] = None
        if status in ("success", "error"):
            estado["fim"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(PROGRESSO_FILE, "w", encoding="utf-8") as f:
            json.dump(dict(estado), f, ensure_ascii=False)
    except Exception:
        pass
    log.info(f"[{progresso}%] {etapa} — {mensagem or erro}")


def get_estado():
    with lock:
        return dict(estado)


# NOTA IMPORTANTE: o agente roda como SERVICO (Session 0) e NUNCA deve
# abrir, matar ou interagir com processos de GUI (status_pdv, vrcheckout).
# Quem gerencia a tela e abre o PDV e o status_pdv.exe, que roda na
# sessao do usuario via Run key e monitora o progresso.json.

def encerrar_processos():
    set_estado("updating", "Encerrando processos", 10)
    for proc in PROCESSOS:
        if processo_rodando(proc):
            subprocess.run(["taskkill", "/F", "/IM", f"{proc}.exe"],
                            capture_output=True)
            for _ in range(10):
                time.sleep(1)
                if not processo_rodando(proc):
                    log.info(f"{proc} encerrado.")
                    break
            else:
                log.warning(f"{proc} pode nao ter encerrado.")
        else:
            log.info(f"{proc} nao estava rodando.")
    time.sleep(2)


def parar_servicos(servicos):
    set_estado("updating", "Parando servicos", 20)
    for svc in servicos:
        st = get_status_servico(svc)
        if st in ("disabled", "nao_existe", "stopped"):
            continue
        subprocess.run(["sc.exe", "stop", svc], capture_output=True)
        for _ in range(15):
            time.sleep(1)
            if get_status_servico(svc) == "stopped":
                log.info(f"{svc} parado.")
                break
        else:
            log.warning(f"{svc} pode nao ter parado.")
    time.sleep(2)


def salvar_banco():
    """Move a pasta db para local seguro antes da atualização."""
    if os.path.exists(DB_DIR):
        if os.path.exists(DB_TEMP_DIR):
            shutil.rmtree(DB_TEMP_DIR)
        shutil.move(DB_DIR, DB_TEMP_DIR)
        log.info(f"Banco movido para: {DB_TEMP_DIR}")
    else:
        log.warning("Pasta db nao encontrada.")


def restaurar_banco():
    """Restaura a pasta db após a atualização."""
    if os.path.exists(DB_TEMP_DIR):
        if os.path.exists(DB_DIR):
            shutil.rmtree(DB_DIR)
        shutil.move(DB_TEMP_DIR, DB_DIR)
        log.info(f"Banco restaurado: {DB_DIR}")
    else:
        log.warning("Backup do banco nao encontrado!")


def fazer_backup():
    set_estado("updating", "Realizando backup", 35)
    salvar_banco()
    if os.path.exists(VRPDV_OLD_DIR):
        shutil.rmtree(VRPDV_OLD_DIR, ignore_errors=True)
    if os.path.exists(VRPDV_DIR):
        arquivos_ignorados = []

        def _copiar(src, dst):
            try:
                shutil.copy2(src, dst)
            except Exception as e:
                arquivos_ignorados.append(os.path.basename(src))
                log.warning(f"Backup ignorou arquivo bloqueado: {src} — {e}")

        shutil.copytree(
            VRPDV_DIR, VRPDV_OLD_DIR,
            ignore=shutil.ignore_patterns("_update.zip"),
            copy_function=_copiar,
        )
        if arquivos_ignorados:
            log.warning(f"Backup incompleto: {len(arquivos_ignorados)} arquivo(s) ignorado(s) — {', '.join(arquivos_ignorados[:5])}")
    log.info("Backup concluido.")


def descompactar():
    set_estado("updating", "Descompactando", 55)
    with zipfile.ZipFile(TEMP_ZIP, "r") as z:
        z.extractall(VRPDV_DIR)
    os.remove(TEMP_ZIP)
    restaurar_banco()
    invalidar_cache_info_pdv()
    log.info("Descompactacao OK.")


def iniciar_servicos(servicos):
    set_estado("updating", "Iniciando servicos", 70)
    falhos = []
    for svc in servicos:
        st = get_status_servico(svc)
        if st in ("disabled", "nao_existe", "running"):
            continue
        log.info(f"Iniciando {svc}...")
        subprocess.run(["sc.exe", "start", svc], capture_output=True)
        for _ in range(25):  # MongoDB pode demorar >3s para subir
            time.sleep(1)
            if get_status_servico(svc) == "running":
                log.info(f"{svc} OK.")
                break
        else:
            log.warning(f"{svc} nao iniciou em 25s — continuando.")
            falhos.append(svc)
    if falhos:
        # Lança só no final para que todos os serviços tenham chance de subir
        raise Exception(f"Servico(s) nao iniciaram: {', '.join(falhos)}")


def verificar_arquivos():
    """Verifica se os arquivos principais foram copiados corretamente."""
    set_estado("updating", "Verificando arquivos", 80)
    arquivos_principais = [
        os.path.join(VRPDV_DIR, "vrcheckout.exe"),
        os.path.join(VRPDV_DIR, "vrpdvapi.exe"),
    ]
    erros = []
    for arq in arquivos_principais:
        if not os.path.exists(arq):
            erros.append(f"AUSENTE: {arq}")
        elif os.path.getsize(arq) == 0:
            erros.append(f"VAZIO: {arq}")
    if erros:
        raise Exception(f"Arquivos corrompidos ou ausentes: {'; '.join(erros)}")
    log.info("Verificacao de arquivos OK.")


def garantir_processos_encerrados():
    """Garante que vrcheckout e vrpdvapi nao estao rodando antes de abrir."""
    set_estado("updating", "Verificando processos", 85)
    for proc in PROCESSOS:
        if processo_rodando(proc):
            log.warning(f"{proc} ainda rodando — forcando encerramento.")
            subprocess.run(["taskkill", "/F", "/IM", f"{proc}.exe"],
                            capture_output=True)
            for _ in range(10):
                time.sleep(1)
                if not processo_rodando(proc):
                    log.info(f"{proc} encerrado.")
                    break
            else:
                raise Exception(f"{proc} nao foi encerrado — abortando abertura do PDV.")
        else:
            log.info(f"{proc} nao esta rodando. OK.")


def iniciar_vrcheckout():
    set_estado("updating", "Aguardando para abrir PDV", 90,
               "Aguardando 10 segundos antes de abrir o PDV...")
    log.info("Aguardando 10 segundos antes de abrir o vrcheckout...")
    time.sleep(10)
    set_estado("updating", "Iniciando vrcheckout", 95)
    log.info("status_pdv.exe abrira o vrcheckout.")
    time.sleep(1)


def _iniciar_na_sessao_usuario(exe_path):
    """Lança processo na sessão interativa do usuário a partir de Session 0.

    Serviços Windows rodam em Session 0 (sem GUI). Para abrir uma janela
    na tela do usuário é necessário usar WTSQueryUserToken + CreateProcessAsUser
    -- a única forma correta de fazer isso no Windows Vista+.
    """
    import ctypes
    import ctypes.wintypes as wt

    try:
        wts      = ctypes.WinDLL("wtsapi32")
        kernel32 = ctypes.WinDLL("kernel32")
        advapi32 = ctypes.WinDLL("advapi32")
        userenv  = ctypes.WinDLL("userenv")

        session_id = kernel32.WTSGetActiveConsoleSessionId()
        if session_id == 0xFFFFFFFF:
            log.warning("Nenhuma sessao interativa ativa — status_pdv nao sera iniciado.")
            return False

        h_token = wt.HANDLE()
        if not wts.WTSQueryUserToken(session_id, ctypes.byref(h_token)):
            log.warning(f"WTSQueryUserToken falhou (err={kernel32.GetLastError()}).")
            return False

        h_dup = wt.HANDLE()
        if not advapi32.DuplicateTokenEx(
            h_token, 0xF01FF, None, 2, 1, ctypes.byref(h_dup)
        ):
            kernel32.CloseHandle(h_token)
            log.warning(f"DuplicateTokenEx falhou (err={kernel32.GetLastError()}).")
            return False

        env_block = ctypes.c_void_p()
        userenv.CreateEnvironmentBlock(ctypes.byref(env_block), h_dup, False)

        class STARTUPINFOW(ctypes.Structure):
            _fields_ = [
                ("cb",              wt.DWORD),  ("lpReserved",      wt.LPWSTR),
                ("lpDesktop",       wt.LPWSTR), ("lpTitle",         wt.LPWSTR),
                ("dwX",             wt.DWORD),  ("dwY",             wt.DWORD),
                ("dwXSize",         wt.DWORD),  ("dwYSize",         wt.DWORD),
                ("dwXCountChars",   wt.DWORD),  ("dwYCountChars",   wt.DWORD),
                ("dwFillAttribute", wt.DWORD),  ("dwFlags",         wt.DWORD),
                ("wShowWindow",     wt.WORD),   ("cbReserved2",     wt.WORD),
                ("lpReserved2",     ctypes.c_char_p),
                ("hStdInput",  wt.HANDLE), ("hStdOutput", wt.HANDLE),
                ("hStdError",  wt.HANDLE),
            ]

        class PROCESS_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("hProcess", wt.HANDLE), ("hThread",     wt.HANDLE),
                ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD),
            ]

        si = STARTUPINFOW()
        si.cb = ctypes.sizeof(si)
        si.lpDesktop = "winsta0\\default"
        si.dwFlags = 0x1   # STARTF_USESHOWWINDOW
        si.wShowWindow = 1  # SW_SHOWNORMAL
        pi = PROCESS_INFORMATION()

        # lpCommandLine deve ser buffer mutavel (Windows pode modificar o buffer)
        cmd_buf = ctypes.create_unicode_buffer(exe_path)

        ok = advapi32.CreateProcessAsUserW(
            h_dup,
            None,      # lpApplicationName
            cmd_buf,   # lpCommandLine (mutavel)
            None, None, False,
            0x420,   # CREATE_UNICODE_ENVIRONMENT | CREATE_NEW_CONSOLE
            env_block, None, ctypes.byref(si), ctypes.byref(pi),
        )

        userenv.DestroyEnvironmentBlock(env_block)
        kernel32.CloseHandle(h_dup)
        kernel32.CloseHandle(h_token)

        if ok:
            kernel32.CloseHandle(pi.hProcess)
            kernel32.CloseHandle(pi.hThread)
            log.info(f"status_pdv.exe iniciado na sessao {session_id}.")
            return True
        else:
            log.warning(f"CreateProcessAsUserW falhou (err={kernel32.GetLastError()}).")
            return False

    except Exception as e:
        log.warning(f"Erro ao iniciar status_pdv na sessao do usuario: {e}")
        return False


def _status_pdv_rodando():
    """Retorna True se o processo status_pdv.exe está ativo."""
    r = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq status_pdv.exe", "/FO", "CSV"],
        capture_output=True, text=True
    )
    return "status_pdv.exe" in r.stdout


def _garantir_status_pdv():
    """Garante que status_pdv.exe está rodando na sessão do usuário.

    Verifica pelo processo (não por porta) para evitar falsos positivos
    causados por outras aplicações que possam usar a mesma porta.
    """
    status_exe = r"C:\PDVAgent\status_pdv.exe"
    if not os.path.exists(status_exe):
        log.warning("status_pdv.exe nao encontrado em C:\\PDVAgent\\")
        return

    # Processo ativo → deixa monitorar o progresso.json e mostrar a janela
    if _status_pdv_rodando():
        log.info("status_pdv.exe ja esta rodando. OK.")
        return

    # Nenhum processo → mata qualquer fantasma e lança nova instância
    log.info("status_pdv.exe nao rodando. Iniciando...")
    subprocess.run(["taskkill", "/F", "/IM", "status_pdv.exe"], capture_output=True)
    time.sleep(0.5)

    # Tenta via Task Scheduler (PDVStatus configurado pelo instalador)
    r = subprocess.run(
        ["schtasks", "/run", "/tn", "PDVStatus"],
        capture_output=True, text=True
    )
    if r.returncode == 0:
        log.info("status_pdv.exe iniciado via schtasks PDVStatus.")
        time.sleep(1)
        return

    # Fallback: WTSQueryUserToken + CreateProcessAsUser (Session 0 → sessão interativa)
    _iniciar_na_sessao_usuario(status_exe)


def executar_atualizacao():
    set_estado("updating", "Iniciando", 0, "Atualizacao iniciada...")
    try:
        _garantir_status_pdv()
        servicos = detectar_servicos()
        encerrar_processos()
        parar_servicos(servicos)
        fazer_backup()
        descompactar()
        verificar_arquivos()
        garantir_processos_encerrados()
        iniciar_servicos(servicos)
        iniciar_vrcheckout()
        set_estado("success", "Concluido", 100, "Atualizacao concluida!")
    except Exception as e:
        log.error(f"ERRO: {e}")
        set_estado("error", "Erro", estado["progresso"], erro=str(e))
