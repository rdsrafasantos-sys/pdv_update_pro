import os
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from werkzeug.middleware.proxy_fix import ProxyFix

from pdv_server.auth.models import init_db
from pdv_server.auth.routes import auth_bp, limiter, login_manager
from pdv_server.config import INTEGRADOR_DATA_DIR, SECRET_KEY
from pdv_server.painel.routes import painel_bp
from pdv_server.discovery import endereco_alcancavel
from pdv_server import VERSION, pdv_compat
from pdv_server.rotas_comuns import com_rede
from pdv_server.routes_agente import agente_bp
from pdv_server.routes_erp import erp_bp
from pdv_server.routes_integrador import integrador_bp
from pdv_server.routes_pdv import pdv_bp
from pdv_server.routes_replicacao import replicacao_bp
from pdv_server.routes_setup import setup_bp

if not SECRET_KEY:
    raise RuntimeError(
        "PDV_SECRET_KEY nao configurada. Gere uma com: "
        "python -c \"import secrets; print(secrets.token_hex(32))\""
    )
# MASTER_KEY (presenca + formato Fernet) ja e validada em config.py na
# importacao -- se chegou ate aqui, esta ok.

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024
app.secret_key = SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Secure flag ativo apenas em produção (HTTPS); staging roda em HTTP
_em_producao = os.environ.get("PDV_AMBIENTE", "prod").lower() not in ("staging", "dev")
app.config["SESSION_COOKIE_SECURE"] = _em_producao

# So confia em X-Forwarded-For/Proto quando ha de fato um proxy reverso na
# frente reescrevendo esse cabecalho (nginx em producao, terminando TLS).
# Em staging o gunicorn fica exposto direto na tailnet -- sem proxy, esse
# cabecalho e forjavel por qualquer peer que alcance a porta, entao NAO
# confiamos nele la (x_for=0 deixa request.remote_addr como veio da conexao).
if _em_producao:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)


@app.after_request
def _headers_seguranca(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self' data:;"
    )
    if _em_producao:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


init_db()
pdv_compat.iniciar_refresh_automatico(INTEGRADOR_DATA_DIR)


@app.route("/api/versao")
def api_versao():
    return jsonify({"versao": VERSION, "ambiente": os.environ.get("PDV_AMBIENTE", "prod")})


login_manager.init_app(app)
limiter.init_app(app)
app.register_blueprint(auth_bp)
app.register_blueprint(painel_bp)
app.register_blueprint(pdv_bp)
app.register_blueprint(agente_bp)
app.register_blueprint(replicacao_bp)
app.register_blueprint(integrador_bp)
app.register_blueprint(erp_bp)
app.register_blueprint(setup_bp)


@app.before_request
def exigir_login():
    rota = request.endpoint or ""
    # Callback do script de instalacao e os downloads/upload publicos rodam
    # sem sessao de usuario -- autenticados por token proprio (ver
    # gestao.py e routes_setup.py._autenticar_setup_upload).
    if rota.startswith("auth.") or rota in (
        "static",
        "painel.api_callback_instalacao",
        "setup.download_agente_publico",
        "setup.download_status_pdv_publico",
        "setup.download_setup_publico",
        "setup.api_upload_setup",
        "api_versao",
    ):
        return None
    if not current_user.is_authenticated:
        return login_manager.unauthorized()
    return None


@app.context_processor
def injetar_usuario():
    return {"usuario_atual": current_user}


@app.route("/")
@login_required
def index():
    return redirect(url_for("painel.redes"))


@app.route("/r/<int:rede_id>/")
@com_rede
def painel_rede(contexto):
    return render_template("index.html", rede_id=contexto.rede_id, rede_nome=contexto.nome,
                           server_version=VERSION,
                           ambiente=os.environ.get("PDV_AMBIENTE", "prod"))


@app.route("/api/<int:rede_id>/sysinfo", methods=["GET"])
@com_rede
def api_sysinfo(contexto):
    """Consulta o agente de monitoramento do service manager (porta 5001).
    O agente é instalado pelo script de instalação da rede."""
    parsed = urlparse(contexto.mongo_uri)
    host_raw = parsed.hostname or ""
    if not host_raw:
        return jsonify({"erro": "Mongo URI não configurado."})
    host = endereco_alcancavel(host_raw, contexto.tailscale_site_id)
    try:
        r = requests.get(f"http://{host}:5001/sysinfo", timeout=3)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"erro": str(e)})
