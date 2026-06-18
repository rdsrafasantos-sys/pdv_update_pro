"""Entrypoint do servico Windows (agente.exe). Roda em Session 0."""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from waitress import serve

from pdv_agent import VERSION
from pdv_agent.config import PORTA
from pdv_agent.logging_setup import configure_logging
from pdv_agent.lmdb_reader import get_info_pdv

log = configure_logging()

if __name__ == "__main__":
    from pdv_agent.app import app

    log.info("=" * 50)
    log.info(f"PDV Agent v{VERSION} iniciando...")
    log.info(f"Porta: {PORTA}")
    threading.Thread(target=get_info_pdv, daemon=True).start()
    log.info("=" * 50)
    serve(app, host="0.0.0.0", port=PORTA)
