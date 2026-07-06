"""Entrypoint do servico Windows (agente.exe). Roda em Session 0."""
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from waitress import serve

from pdv_agent import VERSION
from pdv_agent.config import PORTA
from pdv_agent.logging_setup import configure_logging
from pdv_agent.lmdb_reader import get_info_pdv

log = configure_logging()


def _garantir_mongo_na_inicializacao():
    """Garante que os serviços MongoDB estão rodando após (re)start do agente.

    Roda em background para não atrasar a subida do servidor HTTP.
    Útil especialmente após update do agente, quando os serviços Mongo podem
    ter sido parados por qualquer motivo durante a troca do executável.
    """
    time.sleep(6)  # aguarda o agente subir completamente antes de checar
    try:
        from pdv_agent.service_control import detectar_servicos, get_status_servico
        servicos = detectar_servicos()
        if not servicos:
            return
        for svc in servicos:
            st = get_status_servico(svc)
            if st == "stopped":
                log.info(f"[startup] {svc} parado — reiniciando...")
                subprocess.run(["sc.exe", "start", svc], capture_output=True)
                for _ in range(25):
                    time.sleep(1)
                    if get_status_servico(svc) == "running":
                        log.info(f"[startup] {svc} iniciado com sucesso.")
                        break
                else:
                    log.warning(f"[startup] {svc} nao iniciou em 25s.")
            else:
                log.info(f"[startup] {svc}: {st}")
    except Exception as e:
        log.warning(f"[startup] Erro ao verificar servicos Mongo: {e}")


if __name__ == "__main__":
    from pdv_agent.app import app

    log.info("=" * 50)
    log.info(f"PDV Agent v{VERSION} iniciando...")
    log.info(f"Porta: {PORTA}")
    threading.Thread(target=get_info_pdv, daemon=True).start()
    threading.Thread(target=_garantir_mongo_na_inicializacao, daemon=True).start()
    log.info("=" * 50)
    serve(app, host="0.0.0.0", port=PORTA)
