"""Downloads publicos (agente.exe, status_pdv.exe, PDVAgent_Setup.exe),
upload do instalador completo e status/salvamento da auth key Tailscale
para PDV terminals -- extraido de app.py (Fase 4, divisao por dominio).

Os downloads e o upload de setup rodam SEM sessao de usuario (ver o
allowlist em app.py:exigir_login, que precisa citar os endpoints deste
blueprint pelo nome com o prefixo "setup.")."""
import hmac
import os
import time

from flask import Blueprint, jsonify, request, send_file
from flask_login import current_user, login_required

from pdv_server.auth.audit import registrar_auditoria
from pdv_server.auth.routes import limiter
from pdv_server.rotas_comuns import ip_cliente

setup_bp = Blueprint("setup", __name__)

_SETUP_PATH = "/opt/pdv-server/setup/PDVAgent_Setup.exe"


@setup_bp.route("/download/agente.exe")
@limiter.limit("20 per minute")
def download_agente_publico():
    """Download publico do agente.exe — sem autenticacao, para instalacao inicial em PDVs."""
    import glob
    for caminho in glob.glob("/opt/pdv-server/uploads/*/agente.exe"):
        return send_file(caminho, as_attachment=True, download_name="agente.exe")
    return "agente.exe nao disponivel", 404


@setup_bp.route("/download/status_pdv.exe")
@limiter.limit("20 per minute")
def download_status_pdv_publico():
    """Download publico do status_pdv.exe — sem autenticacao, para instalacao inicial em PDVs."""
    import glob
    for caminho in glob.glob("/opt/pdv-server/uploads/*/status_pdv.exe"):
        return send_file(caminho, as_attachment=True, download_name="status_pdv.exe")
    return "status_pdv.exe nao disponivel", 404


@setup_bp.route("/download/PDVAgent_Setup.exe")
@limiter.limit("20 per minute")
def download_setup_publico():
    """Download público do instalador completo — sem autenticação."""
    if os.path.exists(_SETUP_PATH):
        return send_file(_SETUP_PATH, as_attachment=True, download_name="PDVAgent_Setup.exe")
    return "PDVAgent_Setup.exe não disponível", 404


def _autenticar_setup_upload():
    """Aceita sessão de usuário logado OU token Bearer de deploy."""
    if current_user.is_authenticated:
        return True
    token = os.environ.get("PDV_SETUP_UPLOAD_TOKEN", "")
    auth = request.headers.get("Authorization", "")
    return bool(token) and hmac.compare_digest(auth, f"Bearer {token}")


@setup_bp.route("/api/setup/upload", methods=["POST"])
@limiter.limit("5 per minute")
def api_upload_setup():
    """Recebe PDVAgent_Setup.exe via painel (sessão) ou script de build (token Bearer)."""
    if not _autenticar_setup_upload():
        return jsonify({"erro": "Não autorizado"}), 401
    if "arquivo" not in request.files:
        return jsonify({"erro": "Nenhum arquivo enviado"}), 400
    arquivo = request.files["arquivo"]
    if not arquivo.filename.lower().endswith(".exe"):
        return jsonify({"erro": "Apenas arquivos .exe são aceitos"}), 400
    os.makedirs(os.path.dirname(_SETUP_PATH), exist_ok=True)
    arquivo.save(_SETUP_PATH)
    tamanho = os.path.getsize(_SETUP_PATH)
    _autor = getattr(current_user, "email", None) or "build-script"
    registrar_auditoria(
        _autor, "upload_setup",
        detalhes=f"tamanho={round(tamanho / 1024 / 1024, 2)}MB",
        ip=ip_cliente(),
    )
    return jsonify({
        "ok": True,
        "tamanho_mb": round(tamanho / 1024 / 1024, 2),
        "data": time.strftime("%d/%m/%Y %H:%M"),
    })


@setup_bp.route("/api/setup/info", methods=["GET"])
@login_required
def api_setup_info():
    """Retorna metadados do PDVAgent_Setup.exe disponível no servidor."""
    if not os.path.exists(_SETUP_PATH):
        return jsonify({"disponivel": False})
    return jsonify({
        "disponivel": True,
        "tamanho_mb": round(os.path.getsize(_SETUP_PATH) / 1024 / 1024, 2),
        "data": time.strftime("%d/%m/%Y %H:%M", time.localtime(os.path.getmtime(_SETUP_PATH))),
    })


@setup_bp.route("/api/tailscale/auth-key-pdv", methods=["GET"])
@login_required
def api_auth_key_pdv():
    """Retorna status da auth key PDV (configurada ou não, dias para expirar).
    A key pode vir do arquivo salvo via painel ou das variáveis de ambiente."""
    import datetime
    from pdv_server import pdv_auth_key, tailscale_api

    cfg = pdv_auth_key.ler()
    if not cfg["key"]:
        return jsonify({"erro": "Auth key não configurada."}), 404

    resultado = {
        "dias_restantes": None,
        "nivel_aviso": None,  # None | "ok" | "atencao" | "critico" | "expirada"
    }

    if cfg["key_id"] and tailscale_api.automacao_disponivel():
        try:
            info = tailscale_api.obter_info_key(cfg["key_id"])
            if info.get("invalid"):
                resultado["nivel_aviso"] = "expirada"
                resultado["dias_restantes"] = 0
            else:
                expira_str = info.get("expires", "")
                if expira_str:
                    expira_dt = datetime.datetime.fromisoformat(
                        expira_str.replace("Z", "+00:00")
                    )
                    agora = datetime.datetime.now(datetime.timezone.utc)
                    dias = (expira_dt - agora).days
                    resultado["dias_restantes"] = dias
                    if dias < 0:
                        resultado["nivel_aviso"] = "expirada"
                    elif dias <= 14:
                        resultado["nivel_aviso"] = "critico"
                    elif dias <= 30:
                        resultado["nivel_aviso"] = "atencao"
                    else:
                        resultado["nivel_aviso"] = "ok"
        except Exception:
            pass  # não bloqueia se a checagem de expiração falhar

    return jsonify(resultado)


@setup_bp.route("/api/pdv/auth-key", methods=["POST"])
@login_required
def api_salvar_auth_key_pdv():
    """Salva auth key Tailscale para PDV terminals. Apenas super admins."""
    if not current_user.is_super_admin:
        return jsonify({"erro": "Acesso negado"}), 403
    dados = request.get_json(silent=True) or {}
    key = str(dados.get("key", "")).strip()
    key_id = str(dados.get("key_id", "")).strip()
    if not key:
        return jsonify({"erro": "Auth key obrigatória"}), 400
    if not key.startswith("tskey-auth-"):
        return jsonify({"erro": "Auth key inválida — deve começar com tskey-auth-"}), 400
    from pdv_server import pdv_auth_key
    pdv_auth_key.salvar(key, key_id)
    registrar_auditoria(current_user.email, "salvar_auth_key_pdv", ip=ip_cliente())
    return jsonify({"ok": True})
