import logging
import subprocess

from pdv_agent.config import CONJUNTOS_SERVICOS

log = logging.getLogger("pdv_agent")


def get_status_servico(nome):
    try:
        r = subprocess.run(["sc", "query", nome], capture_output=True, text=True)
        if r.returncode == 1060:
            return "nao_existe"
        r2 = subprocess.run(["sc", "qc", nome], capture_output=True, text=True)
        if "DISABLED" in r2.stdout or "DESATIVADO" in r2.stdout:
            return "disabled"
        if "RUNNING" in r.stdout:
            return "running"
        return "stopped"
    except Exception:
        return "nao_existe"


def detectar_servicos():
    log.info("Detectando servicos...")
    for conjunto in CONJUNTOS_SERVICOS:
        existentes = [s for s in conjunto
                      if get_status_servico(s) not in ("nao_existe", "disabled")]
        if existentes:
            log.info(f"Conjunto: {existentes}")
            return existentes
    return []


def processo_rodando(nome):
    r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {nome}.exe"],
                        capture_output=True, text=True)
    return f"{nome}.exe" in r.stdout
