"""Decorator e helpers compartilhados por todos os blueprints de rotas
/api/<rede_id>/... -- extraido de app.py (era o unico dono) para nao
duplicar em cada blueprint de dominio (routes_pdv.py, routes_replicacao.py,
routes_integrador.py, routes_erp.py, routes_agente.py)."""
from functools import wraps
from urllib.parse import urlparse

from flask import jsonify, redirect, request, url_for
from flask_login import current_user

from pdv_server.auth.gestao import usuario_pode_acessar_rede
from pdv_server.auth.routes import login_manager
from pdv_server.contexto import RedeInativa, RedeNaoEncontrada, obter_contexto


def ip_cliente():
    # request.remote_addr ja vem correto: ProxyFix (app.py) so reescreve
    # a partir de X-Forwarded-For em producao, onde ha um proxy reverso
    # confiavel na frente. Nao ler o cabecalho manualmente aqui -- isso
    # ignoraria essa checagem e voltaria a confiar num valor forjavel.
    return request.remote_addr or ""


def requisicao_mesma_origem():
    """Verifica Origin/Referer contra o host desta requisicao. Mitigacao
    leve de CSRF para rotas GET que disparam acao sensivel e precisam
    continuar em GET (EventSource nativo do navegador nao suporta POST) --
    o projeto nao tem protecao CSRF baseada em token. Origin/Referer
    ausentes sao tratados como suspeitos (fail closed)."""
    origin = request.headers.get("Origin", "")
    if origin:
        return urlparse(origin).netloc == request.host
    referer = request.headers.get("Referer", "")
    if referer:
        return urlparse(referer).netloc == request.host
    return False


def com_rede(view):
    """Carrega o RedeContexto a partir do <rede_id> da URL e injeta como
    primeiro argumento da view, depois de confirmar que o usuario logado
    tem acesso a essa rede (super-admin, acesso_total, ou rede/unidade
    especificamente atribuida a ele -- ver auth/gestao.py)."""
    @wraps(view)
    def wrapper(rede_id, *args, **kwargs):
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        if not usuario_pode_acessar_rede(int(current_user.id), rede_id):
            if request.path.startswith("/api/"):
                return jsonify({"erro": "Sem acesso a esta rede"}), 403
            return redirect(url_for("painel.redes"))
        try:
            contexto = obter_contexto(rede_id)
        except RedeNaoEncontrada:
            if request.path.startswith("/api/"):
                return jsonify({"erro": "Rede nao encontrada"}), 404
            return redirect(url_for("painel.redes"))
        except RedeInativa as e:
            if request.path.startswith("/api/"):
                return jsonify({"erro": str(e)}), 403
            return redirect(url_for("painel.redes"))
        return view(contexto, *args, **kwargs)
    return wrapper
