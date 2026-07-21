"""Configuração do Gunicorn para o PDV Server."""
import os
import sys

# Necessário para o post_fork conseguir importar pdv_server.*
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

bind = f"0.0.0.0:{os.environ.get('PDV_SERVER_PORTA', '8888')}"

# 1 worker garante que as threads de background (loop de replicação,
# pool de Tailscale auth keys) rodem uma única vez — sem duplicatas.
workers = 1
worker_class = "gevent"
worker_connections = 100

# timeout = 0 desabilita o kill automático de workers lentos.
# Necessário: endpoints SSE (status_stream, atualizar_stream) mantêm
# conexões abertas enquanto dura a operação (pode passar de 60s facilmente).
timeout = 0
graceful_timeout = 30
keepalive = 5


def post_fork(server, worker):
    """Inicia as threads de background após o fork do worker gevent."""
    import threading
    from pdv_server.auth.gestao_instalacao import repor_pool_background
    from pdv_server.replication import loop_automatico

    threading.Thread(target=loop_automatico, daemon=True).start()
    threading.Thread(target=repor_pool_background, daemon=True).start()
