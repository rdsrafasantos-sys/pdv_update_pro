import json
import os
import threading
import time
from functools import wraps

import requests
from flask import Flask, Response, jsonify, redirect, render_template, request, url_for
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
)
from pdv_server import erp_db, integrador, replication
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

login_manager.init_app(app)
limiter.init_app(app)
app.register_blueprint(auth_bp)
app.register_blueprint(painel_bp)


@app.before_request
def exigir_login():
    rota = request.endpoint or ""
    # Callback do script de instalacao roda sem sessao de usuario --
    # autenticado pelo token de uso unico na propria URL (ver gestao.py).
    if rota.startswith("auth.") or rota == "static" or rota == "painel.api_callback_instalacao":
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
    return render_template("index.html", rede_id=contexto.rede_id, rede_nome=contexto.nome)


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
        r = requests.get(
            f"http://{endereco_alcancavel(pdv['ip'], contexto.tailscale_site_id)}:5000/ping",
            timeout=3
        )
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
            r = requests.get(
                f"http://{endereco_alcancavel(pdv['ip'], contexto.tailscale_site_id)}:5000/ping",
                timeout=3
            )
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


@app.route("/api/<int:rede_id>/upload_agente", methods=["POST"])
@com_rede
@exigir_escrita
def api_upload_agente(contexto):
    """Recebe o novo agente.exe e salva no servidor."""
    if "arquivo" not in request.files:
        return jsonify({"erro": "Nenhum arquivo enviado"}), 400
    arquivo = request.files["arquivo"]
    if not arquivo.filename.endswith(".exe"):
        return jsonify({"erro": "Apenas arquivos .exe sao aceitos"}), 400
    caminho = os.path.join(contexto.upload_dir, "agente.exe")
    arquivo.save(caminho)
    tamanho = os.path.getsize(caminho)
    return jsonify({
        "mensagem": "Upload do agente concluido",
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

    resultados = enviar_agente_para_pdvs(contexto, caminho_exe, pdvs_alvo)
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
