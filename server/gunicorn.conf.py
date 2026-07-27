"""Configuração do Gunicorn para o PDV Server."""
# monkey.patch_all() com ssl=True (padrao) nessa combinacao de versoes
# (gevent 26.5.0 + Python 3.12) quebra qualquer chamada HTTPS de saida com
# "RecursionError: maximum recursion depth exceeded" dentro de
# ssl.SSLContext.minimum_version -- reproduzido tanto com o patch feito cedo
# quanto tarde, entao nao e um problema de ORDEM de import, e sim do proprio
# patch de ssl nessa versao. monkey.patch_all(ssl=False) sozinho nao resolve
# porque o proprio worker "gevent" do gunicorn (gunicorn/workers/ggevent.py)
# chama monkey.patch_all() de novo com os padroes (ssl=True) ao carregar a
# classe do worker, depois do nosso -- ver _corrigir_ssl_gevent() abaixo.
from gevent import monkey
monkey.patch_all(ssl=False)

import os
import sys


def _corrigir_ssl_gevent():
    """Contorno cirurgico pro RecursionError acima: troca
    urllib3.util.ssl_.create_urllib3_context por uma versao que nunca
    atribui minimum_version/maximum_version (as duas linhas que recursam
    infinitamente com o ssl remendado pelo gevent) -- o resto da funcao
    (cifras, opcoes, verify_mode, check_hostname) fica identico ao
    original. PROTOCOL_TLS_CLIENT ja exige TLS 1.2+ por padrao no OpenSSL
    moderno, entao a postura de seguranca efetiva nao muda."""
    import ssl as _ssl
    import urllib3.util.ssl_ as _ssl_util

    def _create_urllib3_context(
        ssl_version=None, cert_reqs=None, options=None, ciphers=None,
        ssl_minimum_version=None, ssl_maximum_version=None, verify_flags=None,
    ):
        context = _ssl.SSLContext(_ssl_util.PROTOCOL_TLS_CLIENT)
        if ciphers:
            context.set_ciphers(ciphers)
        if options is None:
            options = (
                _ssl_util.OP_NO_SSLv2 | _ssl_util.OP_NO_SSLv3
                | _ssl_util.OP_NO_COMPRESSION | _ssl_util.OP_NO_TICKET
            )
        context.options |= options
        cert_reqs = _ssl.CERT_REQUIRED if cert_reqs is None else cert_reqs
        if cert_reqs == _ssl.CERT_REQUIRED:
            context.verify_mode = cert_reqs
            context.check_hostname = True
        else:
            context.check_hostname = False
            context.verify_mode = cert_reqs
        return context

    _ssl_util.create_urllib3_context = _create_urllib3_context


_corrigir_ssl_gevent()

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
    _corrigir_ssl_gevent()  # idempotente -- garante que vale no processo do worker de fato

    import threading
    from pdv_server.auth.gestao_instalacao import repor_pool_background
    from pdv_server.replication import loop_automatico

    threading.Thread(target=loop_automatico, daemon=True).start()
    threading.Thread(target=repor_pool_background, daemon=True).start()
