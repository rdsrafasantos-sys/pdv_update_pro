import json
import logging
import os
import shutil
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
        subprocess.run(["sc", "stop", svc], capture_output=True)
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
        shutil.rmtree(VRPDV_OLD_DIR)
    if os.path.exists(VRPDV_DIR):
        shutil.copytree(VRPDV_DIR, VRPDV_OLD_DIR,
                         ignore=shutil.ignore_patterns("_update.zip"))
    log.info("Backup OK.")


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
    for svc in servicos:
        st = get_status_servico(svc)
        if st in ("disabled", "nao_existe", "running"):
            continue
        log.info(f"Iniciando {svc}...")
        subprocess.run(["sc", "start", svc], capture_output=True)
        time.sleep(3)
        if get_status_servico(svc) != "running":
            raise Exception(f"{svc} nao iniciou")
        log.info(f"{svc} OK.")


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


def executar_atualizacao():
    set_estado("updating", "Iniciando", 0, "Atualizacao iniciada...")
    try:
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
