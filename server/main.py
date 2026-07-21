"""Entrypoint do PDV Server. Roda no Service Manager Ubuntu (systemd)."""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from pdv_server.app import app
from pdv_server.auth.gestao_instalacao import repor_pool_background
from pdv_server.config import PORTA_SERVIDOR
from pdv_server.replication import loop_automatico

if __name__ == "__main__":
    print("=" * 50)
    print("PDV Server iniciando...")
    print(f"Acesse: http://localhost:{PORTA_SERVIDOR}")
    print("=" * 50)
    threading.Thread(target=loop_automatico, daemon=True).start()
    # Pre-enche o pool de auth keys para instalacoes (sem bloquear startup)
    threading.Thread(target=repor_pool_background, daemon=True).start()
    app.run(host="0.0.0.0", port=PORTA_SERVIDOR, debug=False)
