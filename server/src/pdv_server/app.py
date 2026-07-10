import json
import os
import threading
import time
from functools import wraps

import requests
from flask import Flask, Response, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from pdv_server.auth.gestao import usuario_pode_acessar_rede
from pdv_server.auth.models import init_db
from pdv_server.auth.routes import auth_bp, exigir_escrita, limiter, login_manager
from pdv_server.config import MASTER_KEY, SECRET_KEY
from pdv_server.contexto import RedeInativa, RedeNaoEncontrada, obter_contexto
from pdv_server.painel.routes import painel_bp
from pdv_server.dispatch import (
    enviar_agente_para_pdvs, get_atualizacoes_loja, iniciar_envio_zip,
    reiniciar_mongo_pdv,
)
from pdv_server.discovery import (
    encontrar_pdv, endereco_alcancavel, get_lojas, invalidar_cache,
    resolver_endereco, _tentar_requisicao,
)
from pdv_server import VERSION, erp_db, integrador, integrador_update, replication
from pdv_server.versioning import eh_downgrade, extrair_versao

if not SECRET_KEY:
    raise RuntimeError(
        "PDV_SECRET_KEY nao configurada. Gere uma com: "
        "python -c \"import secrets; print(secrets.token_hex(32))\""
    )
if not MASTER_KEY:
    raise RuntimeError(
        "PDV_MASTER_KEY nao configurada. Gere uma com: "
        "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
    )

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024
app.secret_key = SECRET_KEY

init_db()


@app.route("/api/versao")
def api_versao():
    return jsonify({"versao": VERSION, "ambiente": os.environ.get("PDV_AMBIENTE", "prod")})

login_manager.init_app(app)
limiter.init_app(app)
app.register_blueprint(auth_bp)
app.register_blueprint(painel_bp)


@app.before_request
def exigir_login():
    rota = request.endpoint or ""
    # Callback do script de instalacao roda sem sessao de usuario --
    # autenticado pelo token de uso unico na propria URL (ver gestao.py).
    if rota.startswith("auth.") or rota in ("static", "painel.api_callback_instalacao", "download_agente_publico", "download_status_pdv_publico", "download_setup_publico", "api_upload_setup", "api_versao"):
        return None
    if not current_user.is_authenticated:
        return login_manager.unauthorized()
    return None


@app.context_processor
def injetar_usuario():
    return {"usuario_atual": current_user}


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


@app.route("/api/<int:rede_id>/lojas", methods=["GET"])
@com_rede
def api_lojas(contexto):
    return jsonify(get_lojas(contexto))


@app.route("/api/<int:rede_id>/lojas/atualizar", methods=["POST"])
@com_rede
def api_lojas_atualizar(contexto):
    """Força redescoberta dos PDVs via replica set."""
    invalidar_cache(contexto.rede_id)
    return jsonify({"mensagem": "Cache invalidado", "lojas": get_lojas(contexto)})


@app.route("/api/<int:rede_id>/ping/<loja_id>/<pdv_id>", methods=["GET"])
@com_rede
def api_ping(contexto, loja_id, pdv_id):
    pdv = encontrar_pdv(contexto, loja_id, pdv_id)
    if not pdv:
        return jsonify({"erro": "PDV não encontrado"}), 404
    try:
        r, _ = _tentar_requisicao(pdv["ip"], contexto.tailscale_site_id, "5000/ping", timeout=3)
        dados = r.json() if r.status_code == 200 else {}
        return jsonify({
            "online": r.status_code == 200,
            "ip": pdv["ip"],
            "versao_agente": dados.get("versao", "—")
        })
    except Exception:
        return jsonify({"online": False, "ip": pdv["ip"], "versao_agente": "—"})


@app.route("/api/<int:rede_id>/ping_loja/<loja_id>", methods=["GET"])
@com_rede
def api_ping_loja(contexto, loja_id):
    loja = next((l for l in get_lojas(contexto) if l["id"] == loja_id), None)
    if not loja:
        return jsonify({"erro": "Loja não encontrada"}), 404

    resultados = {}
    threads = []

    def checar(pdv):
        try:
            r, _ = _tentar_requisicao(pdv["ip"], contexto.tailscale_site_id, "5000/ping", timeout=3)
            dados = r.json() if r.status_code == 200 else {}
            resultados[pdv["id"]] = {
                "online": r.status_code == 200,
                "versao_agente": dados.get("versao", "—")
            }
        except Exception:
            resultados[pdv["id"]] = {"online": False, "versao_agente": "—"}

    for pdv in loja["pdvs"]:
        t = threading.Thread(target=checar, args=(pdv,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=5)

    return jsonify(resultados)


@app.route("/api/<int:rede_id>/pdv/<loja_id>/<pdv_id>/reiniciar_mongo", methods=["POST"])
@com_rede
@exigir_escrita
def api_reiniciar_mongo(contexto, loja_id, pdv_id):
    pdv = encontrar_pdv(contexto, loja_id, pdv_id)
    if not pdv:
        return jsonify({"erro": "PDV não encontrado"}), 404
    resultado = reiniciar_mongo_pdv(contexto, pdv)
    return jsonify(resultado)


@app.route("/api/<int:rede_id>/upload", methods=["POST"])
@com_rede
@exigir_escrita
def api_upload(contexto):
    if "arquivo" not in request.files:
        return jsonify({"erro": "Nenhum arquivo enviado"}), 400
    arquivo = request.files["arquivo"]
    if not arquivo.filename.endswith(".zip"):
        return jsonify({"erro": "Apenas arquivos .zip são aceitos"}), 400
    nome = secure_filename(arquivo.filename)
    caminho = os.path.join(contexto.upload_dir, nome)
    arquivo.save(caminho)
    tamanho = os.path.getsize(caminho)
    return jsonify({
        "mensagem": "Upload concluído",
        "arquivo": nome,
        "tamanho_mb": round(tamanho / 1024 / 1024, 2)
    })


@app.route("/api/<int:rede_id>/arquivos", methods=["GET"])
@com_rede
def api_arquivos(contexto):
    arquivos = []
    for f in os.listdir(contexto.upload_dir):
        if f.endswith(".zip"):
            caminho = os.path.join(contexto.upload_dir, f)
            arquivos.append({
                "nome": f,
                "tamanho_mb": round(os.path.getsize(caminho) / 1024 / 1024, 2),
                "data": time.strftime("%d/%m/%Y %H:%M",
                                       time.localtime(os.path.getmtime(caminho))),
                "versao": extrair_versao(f)
            })
    arquivos.sort(key=lambda x: x["data"], reverse=True)
    return jsonify(arquivos)


@app.route("/api/<int:rede_id>/arquivos/<nome>", methods=["DELETE"])
@com_rede
@exigir_escrita
def api_deletar_arquivo(contexto, nome):
    caminho = os.path.join(contexto.upload_dir, secure_filename(nome))
    if os.path.exists(caminho):
        os.remove(caminho)
        return jsonify({"mensagem": f"{nome} removido"})
    return jsonify({"erro": "Arquivo não encontrado"}), 404


@app.route("/api/<int:rede_id>/arquivos/limpar", methods=["DELETE"])
@com_rede
@exigir_escrita
def api_limpar_arquivos(contexto):
    removidos = 0
    for f in os.listdir(contexto.upload_dir):
        if f.endswith(".zip"):
            os.remove(os.path.join(contexto.upload_dir, f))
            removidos += 1
    return jsonify({"mensagem": f"{removidos} arquivo(s) removido(s)"})


@app.route("/download/agente.exe")
def download_agente_publico():
    """Download publico do agente.exe — sem autenticacao, para instalacao inicial em PDVs."""
    import glob
    for caminho in glob.glob("/opt/pdv-server/uploads/*/agente.exe"):
        return send_file(caminho, as_attachment=True, download_name="agente.exe")
    return "agente.exe nao disponivel", 404


@app.route("/download/status_pdv.exe")
def download_status_pdv_publico():
    """Download publico do status_pdv.exe — sem autenticacao, para instalacao inicial em PDVs."""
    import glob
    for caminho in glob.glob("/opt/pdv-server/uploads/*/status_pdv.exe"):
        return send_file(caminho, as_attachment=True, download_name="status_pdv.exe")
    return "status_pdv.exe nao disponivel", 404


_SETUP_PATH = "/opt/pdv-server/setup/PDVAgent_Setup.exe"


@app.route("/download/PDVAgent_Setup.exe")
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
    return token and auth == f"Bearer {token}"


@app.route("/api/setup/upload", methods=["POST"])
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
    return jsonify({
        "ok": True,
        "tamanho_mb": round(tamanho / 1024 / 1024, 2),
        "data": time.strftime("%d/%m/%Y %H:%M"),
    })


@app.route("/api/setup/info", methods=["GET"])
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


@app.route("/api/token-agente", methods=["GET"])
@login_required
def api_token_agente():
    """Retorna o token de acesso dos agentes PDV (PDV_SERVER_TOKEN).
    Usado pelo painel de Instalação para exibir o valor ao técnico."""
    from pdv_server.config import TOKEN_SEGURANCA
    return jsonify({"token": TOKEN_SEGURANCA})


@app.route("/api/tailscale/auth-key-pdv", methods=["GET"])
@login_required
def api_auth_key_pdv():
    """Retorna a auth key PDV configurada no .env e, se o ID também estiver
    configurado e a API Tailscale disponível, informa quantos dias restam
    para expirar (para o aviso no painel)."""
    import datetime
    from pdv_server import tailscale_api
    from pdv_server.config import TAILSCALE_AUTH_KEY_PDV, TAILSCALE_AUTH_KEY_PDV_ID

    if not TAILSCALE_AUTH_KEY_PDV:
        return jsonify({
            "erro": "PDV_TAILSCALE_AUTH_KEY_PDV não configurado. "
                    "Crie uma auth key com tag:pdv-terminal no admin console do Tailscale "
                    "e adicione no .env do servidor."
        }), 404

    resultado = {
        "key": TAILSCALE_AUTH_KEY_PDV,
        "dias_restantes": None,
        "nivel_aviso": None,  # None | "ok" | "atencao" | "critico" | "expirada"
    }

    if TAILSCALE_AUTH_KEY_PDV_ID and tailscale_api.automacao_disponivel():
        try:
            info = tailscale_api.obter_info_key(TAILSCALE_AUTH_KEY_PDV_ID)
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
            pass  # não bloqueia exibição da key se a checagem de expiração falhar

    return jsonify(resultado)


@app.route("/api/agente/info", methods=["GET"])
@login_required
def api_agente_info():
    """Retorna metadados dos instaladores disponíveis para download (agente + status_pdv)."""
    import glob as _glob

    def _info(nome):
        candidatos = _glob.glob(f"/opt/pdv-server/uploads/*/{nome}")
        if not candidatos:
            return {"disponivel": False}
        caminho = max(candidatos, key=os.path.getmtime)
        return {
            "disponivel": True,
            "tamanho_mb": round(os.path.getsize(caminho) / 1024 / 1024, 2),
            "data": time.strftime("%d/%m/%Y %H:%M", time.localtime(os.path.getmtime(caminho))),
        }

    return jsonify({
        "agente": _info("agente.exe"),
        "status_pdv": _info("status_pdv.exe"),
    })


@app.route("/api/<int:rede_id>/upload_agente", methods=["POST"])
@com_rede
@exigir_escrita
def api_upload_agente(contexto):
    """Recebe agente.exe ou status_pdv.exe e salva no servidor."""
    if "arquivo" not in request.files:
        return jsonify({"erro": "Nenhum arquivo enviado"}), 400
    arquivo = request.files["arquivo"]
    nome = arquivo.filename.lower()
    if nome not in ("agente.exe", "status_pdv.exe"):
        return jsonify({"erro": "Apenas agente.exe ou status_pdv.exe sao aceitos"}), 400
    caminho = os.path.join(contexto.upload_dir, nome)
    arquivo.save(caminho)
    tamanho = os.path.getsize(caminho)
    return jsonify({
        "mensagem": f"Upload de {nome} concluido",
        "tamanho_mb": round(tamanho / 1024 / 1024, 2)
    })


@app.route("/api/<int:rede_id>/versao_agente", methods=["GET"])
@com_rede
def api_versao_agente(contexto):
    """Verifica se existe agente.exe disponivel para distribuicao."""
    caminho = os.path.join(contexto.upload_dir, "agente.exe")
    if os.path.exists(caminho):
        return jsonify({
            "disponivel": True,
            "tamanho_mb": round(os.path.getsize(caminho) / 1024 / 1024, 2),
            "data": time.strftime("%d/%m/%Y %H:%M",
                                   time.localtime(os.path.getmtime(caminho)))
        })
    return jsonify({"disponivel": False})


@app.route("/api/<int:rede_id>/atualizar_agente", methods=["POST"])
@com_rede
@exigir_escrita
def api_atualizar_agente(contexto):
    """Envia novo agente.exe para PDVs selecionados."""
    dados = request.json
    loja_id = dados.get("loja_id")
    pdv_ids = dados.get("pdv_ids", [])

    caminho_exe = os.path.join(contexto.upload_dir, "agente.exe")
    if not os.path.exists(caminho_exe):
        return jsonify({"erro": "Nenhum agente.exe disponivel. Faca upload primeiro."}), 404

    loja = next((l for l in get_lojas(contexto) if l["id"] == loja_id), None)
    if not loja:
        return jsonify({"erro": "Loja nao encontrada"}), 404

    pdvs_alvo = loja["pdvs"] if pdv_ids == "todos" else \
        [p for p in loja["pdvs"] if p["id"] in pdv_ids]

    if not pdvs_alvo:
        return jsonify({"erro": "Nenhum PDV selecionado"}), 400

    caminho_status = os.path.join(contexto.upload_dir, "status_pdv.exe")
    resultados = enviar_agente_para_pdvs(
        contexto, caminho_exe, pdvs_alvo,
        caminho_status=caminho_status if os.path.exists(caminho_status) else None
    )
    return jsonify({"resultados": resultados})


@app.route("/api/<int:rede_id>/atualizar", methods=["POST"])
@com_rede
@exigir_escrita
def api_atualizar(contexto):
    dados = request.json
    loja_id = dados.get("loja_id")
    pdv_ids = dados.get("pdv_ids", [])
    arquivo = dados.get("arquivo")

    if not loja_id or not arquivo:
        return jsonify({"erro": "loja_id e arquivo são obrigatórios"}), 400

    if pdv_ids == "todos" or len(pdv_ids) != 1:
        return jsonify({"erro": "Selecione exatamente um PDV por vez para atualizar."}), 400

    caminho_zip = os.path.join(contexto.upload_dir, arquivo)
    if not os.path.exists(caminho_zip):
        return jsonify({"erro": f"Arquivo {arquivo} não encontrado"}), 404

    loja = next((l for l in get_lojas(contexto) if l["id"] == loja_id), None)
    if not loja:
        return jsonify({"erro": "Loja não encontrada"}), 404

    pdvs_alvo = [p for p in loja["pdvs"] if p["id"] in pdv_ids]
    if not pdvs_alvo:
        return jsonify({"erro": "Nenhum PDV selecionado"}), 400

    pdv = pdvs_alvo[0]
    versao_zip = extrair_versao(arquivo)
    if not versao_zip:
        return jsonify({
            "erro": "Não foi possível identificar a versão pelo nome do arquivo "
                    "(ex: VRPdvPro_7.1.0.zip). Renomeie o arquivo e envie novamente."
        }), 400

    versao_pdv = pdv.get("versao")
    if eh_downgrade(versao_zip, versao_pdv):
        return jsonify({
            "erro": f"Atualização bloqueada: o pacote é da versão {versao_zip}, mas o "
                    f"PDV {pdv['id']} já está na versão {versao_pdv}. Downgrade não é "
                    f"permitido (risco de corromper o banco)."
        }), 409

    iniciar_envio_zip(contexto, loja_id, pdv, caminho_zip)

    return jsonify({
        "mensagem": f"Atualização iniciada para {pdv['id']}",
        "pdvs": [pdv["id"]]
    })


@app.route("/api/<int:rede_id>/status/<loja_id>", methods=["GET"])
@com_rede
def api_status_loja(contexto, loja_id):
    return jsonify(get_atualizacoes_loja(contexto.rede_id, loja_id))


@app.route("/api/<int:rede_id>/status_stream/<loja_id>")
@com_rede
def api_status_stream(contexto, loja_id):
    rede_id = contexto.rede_id

    def gerar():
        ultimo = None
        while True:
            atual = json.dumps(get_atualizacoes_loja(rede_id, loja_id))
            if atual != ultimo:
                ultimo = atual
                yield f"data: {atual}\n\n"
            time.sleep(1)
    return Response(gerar(), mimetype="text/event-stream",
                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ──────────────────────────────────────────────
# VERIFICACAO DE REPLICACAO
# ──────────────────────────────────────────────
@app.route("/api/<int:rede_id>/replicacao/verificar", methods=["POST"])
@com_rede
@exigir_escrita
def api_replicacao_verificar(contexto):
    dados = request.json or {}
    loja_id = dados.get("loja_id")
    pdv_ids = dados.get("pdv_ids", [])

    loja = next((l for l in get_lojas(contexto) if l["id"] == loja_id), None)
    if not loja:
        return jsonify({"erro": "Loja nao encontrada"}), 404

    pdvs_alvo = loja["pdvs"] if pdv_ids == "todos" else \
        [p for p in loja["pdvs"] if p["id"] in pdv_ids]
    if not pdvs_alvo:
        return jsonify({"erro": "Nenhum PDV selecionado"}), 400

    replication.iniciar_verificacao_lote(contexto, loja_id, pdvs_alvo, tipo="manual")

    return jsonify({
        "mensagem": f"Verificacao de replicacao iniciada para {len(pdvs_alvo)} PDV(s)",
        "pdvs": [p["id"] for p in pdvs_alvo]
    })


@app.route("/api/<int:rede_id>/replicacao/status/<loja_id>/<pdv_id>", methods=["GET"])
@com_rede
def api_replicacao_status(contexto, loja_id, pdv_id):
    return jsonify(replication.get_estado(contexto.rede_id, loja_id, pdv_id))


@app.route("/api/<int:rede_id>/replicacao/config", methods=["GET"])
@com_rede
def api_replicacao_config_get(contexto):
    return jsonify(replication.carregar_config_auto(contexto))


@app.route("/api/<int:rede_id>/replicacao/config", methods=["POST"])
@com_rede
@exigir_escrita
def api_replicacao_config_set(contexto):
    dados = request.json or {}
    alteracoes = {k: dados[k] for k in ("habilitado", "intervalo_minutos", "pdvs") if k in dados}
    return jsonify(replication.salvar_config_auto(contexto, alteracoes))


@app.route("/api/<int:rede_id>/replicacao/historico", methods=["GET"])
@com_rede
def api_replicacao_historico(contexto):
    return jsonify(replication.obter_historico(contexto))


@app.route("/r/<int:rede_id>/replicacao/detalhe/<loja_id>/<pdv_id>/<colecao>")
@com_rede
def replicacao_detalhe(contexto, loja_id, pdv_id, colecao):
    """Pagina em aba separada com o conteudo completo dos documentos
    divergentes de uma colecao (consome o mesmo /api/.../replicacao/status)."""
    return render_template(
        "replicacao_detalhe.html", rede_id=contexto.rede_id,
        loja_id=loja_id, pdv_id=pdv_id, colecao=colecao
    )


# ──────────────────────────────────────────────
# BANCO DE DADOS DO ERP (PostgreSQL) — Configuracoes
# ──────────────────────────────────────────────
@app.route("/api/<int:rede_id>/erp_db/config", methods=["GET"])
@com_rede
def api_erp_db_config_get(contexto):
    return jsonify(erp_db.carregar_config(contexto))


@app.route("/api/<int:rede_id>/erp_db/config", methods=["POST"])
@com_rede
@exigir_escrita
def api_erp_db_config_set(contexto):
    dados = request.json or {}
    return jsonify(erp_db.salvar_config(contexto, dados))


@app.route("/api/<int:rede_id>/erp_db/status", methods=["GET"])
@com_rede
def api_erp_db_status(contexto):
    return jsonify(erp_db.testar_conexao(contexto))


@app.route("/api/<int:rede_id>/erp_db/pdvs_ativos", methods=["GET"])
@com_rede
def api_erp_db_pdvs_ativos(contexto):
    return jsonify(erp_db.listar_pdvs_ativos(contexto))


@app.route("/api/<int:rede_id>/erp_db/pendencias_fiscais", methods=["GET"])
@com_rede
def api_erp_db_pendencias_fiscais(contexto):
    return jsonify(erp_db.pendencias_fiscais(contexto))


# ──────────────────────────────────────────────
# INTEGRADOR VR — Configuracoes
# ──────────────────────────────────────────────
@app.route("/api/<int:rede_id>/integrador/config", methods=["GET"])
@com_rede
def api_integrador_config_get(contexto):
    return jsonify(integrador.carregar_config(contexto))


@app.route("/api/<int:rede_id>/integrador/config", methods=["POST"])
@com_rede
@exigir_escrita
def api_integrador_config_set(contexto):
    dados = request.json or {}
    return jsonify(integrador.salvar_config(contexto, dados))


@app.route("/api/<int:rede_id>/integrador/status", methods=["GET"])
@com_rede
def api_integrador_status(contexto):
    return jsonify(integrador.testar_status(contexto))


# ──────────────────────────────────────────────
# INTEGRADOR VR — Atualização via SSH
# ──────────────────────────────────────────────
@app.route("/api/<int:rede_id>/integrador/versao_atual", methods=["GET"])
@com_rede
def api_integrador_versao_atual(contexto):
    cfg = integrador.carregar_config(contexto)
    if not integrador_update.config_ssh_completa(cfg):
        return jsonify({"erro": "SSH não configurado", "versao": None})
    return jsonify(integrador_update.versao_atual(cfg))


@app.route("/api/<int:rede_id>/integrador/atualizar_stream", methods=["GET"])
@com_rede
def api_integrador_atualizar_stream(contexto):
    nova_versao = request.args.get("versao", "").strip()
    if not nova_versao:
        return jsonify({"erro": "Parâmetro 'versao' obrigatório"}), 400
    cfg = integrador.carregar_config(contexto)
    if not integrador_update.config_ssh_completa(cfg):
        return jsonify({"erro": "SSH não configurado"}), 400

    import json as _json

    def gerar():
        for evento in integrador_update.atualizar_stream(cfg, nova_versao):
            yield f"data: {_json.dumps(evento, ensure_ascii=False)}\n\n"

    return app.response_class(gerar(), mimetype="text/event-stream",
                              headers={"Cache-Control": "no-cache",
                                       "X-Accel-Buffering": "no"})


@app.route("/api/<int:rede_id>/sysinfo_loja/<loja_id>", methods=["GET"])
@com_rede
def api_sysinfo_loja(contexto, loja_id):
    """Consulta /sysinfo do agente de todos os PDVs de uma loja em paralelo."""
    loja = next((l for l in get_lojas(contexto) if l["id"] == loja_id), None)
    if not loja:
        return jsonify({}), 404
    resultados = {}
    threads = []

    def checar(pdv):
        try:
            r, _ = _tentar_requisicao(pdv["ip"], contexto.tailscale_site_id, "5000/sysinfo", timeout=3)
            resultados[pdv["id"]] = r.json()
        except Exception:
            resultados[pdv["id"]] = {"erro": "sem resposta"}

    for pdv in loja["pdvs"]:
        t = threading.Thread(target=checar, args=(pdv,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join(timeout=5)
    return jsonify(resultados)


@app.route("/api/<int:rede_id>/sysinfo", methods=["GET"])
@com_rede
def api_sysinfo(contexto):
    """Consulta o agente de monitoramento do service manager (porta 5001).
    O agente é instalado pelo script de instalação da rede."""
    from urllib.parse import urlparse
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


@app.route("/api/<int:rede_id>/erp_db/lojas", methods=["GET"])
@com_rede
def api_erp_db_lojas(contexto):
    """Retorna lojas do ERP: apelido (tabela loja) + nome e CNPJ (tabela fornecedor)."""
    cfg = erp_db.carregar_config(contexto)
    if not cfg.get("host") or not cfg.get("banco"):
        return jsonify({"erro": "ERP não configurado.", "lojas": []})
    try:
        conn = erp_db._conectar(cfg, contexto.tailscale_site_id)
        try:
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = '8000'")
                # Tenta join com fornecedor; se falhar, retorna só a tabela loja
                try:
                    cur.execute("""
                        SELECT l.id, l.descricao, f.nomefantasia, f.cnpj
                        FROM loja l
                        LEFT JOIN fornecedor f ON f.id = l.id_fornecedor
                        ORDER BY l.id
                    """)
                    linhas = cur.fetchall()
                    lojas_ret = [
                        {"id": r[0], "apelido": r[1] or "", "nome": r[2] or "", "cnpj": r[3] or ""}
                        for r in linhas
                    ]
                except Exception:
                    # Fallback: so a tabela loja sem join
                    conn.rollback()
                    cur.execute("SELECT id, descricao FROM loja ORDER BY id")
                    linhas = cur.fetchall()
                    lojas_ret = [
                        {"id": r[0], "apelido": r[1] or "", "nome": r[1] or "", "cnpj": ""}
                        for r in linhas
                    ]
        finally:
            conn.close()
        return jsonify({"lojas": lojas_ret})
    except Exception as e:
        return jsonify({"erro": str(e), "lojas": []})


@app.route("/api/<int:rede_id>/erp_db/stats", methods=["GET"])
@com_rede
def api_erp_db_stats(contexto):
    """Retorna estatísticas do banco PostgreSQL do ERP."""
    cfg = erp_db.carregar_config(contexto)
    if not cfg.get("host") or not cfg.get("banco"):
        return jsonify({"erro": "ERP não configurado."})
    try:
        conn = erp_db._conectar(cfg, contexto.tailscale_site_id)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT version()")
                row = cur.fetchone()
                versao = row[0].split()[1] if row else "?"
                cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
                tamanho = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM pg_stat_activity WHERE state = 'active'")
                conexoes = cur.fetchone()[0]
        finally:
            conn.close()
        return jsonify({"versao": versao, "tamanho_bd": tamanho, "conexoes_ativas": int(conexoes)})
    except Exception as e:
        return jsonify({"erro": str(e)})
