import json
import os
import threading
import time

import requests
from flask import Flask, Response, jsonify, render_template, request
from werkzeug.utils import secure_filename

from pdv_server.config import TOKEN_SEGURANCA, UPLOAD_DIR
from pdv_server.dispatch import (
    enviar_agente_para_pdvs, get_atualizacoes_loja, iniciar_envio_zip,
    reiniciar_mongo_pdv,
)
from pdv_server.discovery import (
    encontrar_pdv, endereco_alcancavel, get_lojas, invalidar_cache,
)
from pdv_server import erp_db, integrador, replication
from pdv_server.versioning import eh_downgrade, extrair_versao

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024

os.makedirs(UPLOAD_DIR, exist_ok=True)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/lojas", methods=["GET"])
def api_lojas():
    return jsonify(get_lojas())


@app.route("/api/lojas/atualizar", methods=["POST"])
def api_lojas_atualizar():
    """Força redescoberta dos PDVs via replica set."""
    invalidar_cache()
    return jsonify({"mensagem": "Cache invalidado", "lojas": get_lojas()})


@app.route("/api/ping/<loja_id>/<pdv_id>", methods=["GET"])
def api_ping(loja_id, pdv_id):
    pdv = encontrar_pdv(loja_id, pdv_id)
    if not pdv:
        return jsonify({"erro": "PDV não encontrado"}), 404
    try:
        r = requests.get(f"http://{endereco_alcancavel(pdv['ip'])}:5000/ping", timeout=3)
        dados = r.json() if r.status_code == 200 else {}
        return jsonify({
            "online": r.status_code == 200,
            "ip": pdv["ip"],
            "versao_agente": dados.get("versao", "—")
        })
    except Exception:
        return jsonify({"online": False, "ip": pdv["ip"], "versao_agente": "—"})


@app.route("/api/ping_loja/<loja_id>", methods=["GET"])
def api_ping_loja(loja_id):
    loja = next((l for l in get_lojas() if l["id"] == loja_id), None)
    if not loja:
        return jsonify({"erro": "Loja não encontrada"}), 404

    resultados = {}
    threads = []

    def checar(pdv):
        try:
            r = requests.get(f"http://{endereco_alcancavel(pdv['ip'])}:5000/ping", timeout=3)
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


@app.route("/api/pdv/<loja_id>/<pdv_id>/reiniciar_mongo", methods=["POST"])
def api_reiniciar_mongo(loja_id, pdv_id):
    pdv = encontrar_pdv(loja_id, pdv_id)
    if not pdv:
        return jsonify({"erro": "PDV não encontrado"}), 404
    resultado = reiniciar_mongo_pdv(pdv)
    return jsonify(resultado)


@app.route("/api/upload", methods=["POST"])
def api_upload():
    if "arquivo" not in request.files:
        return jsonify({"erro": "Nenhum arquivo enviado"}), 400
    arquivo = request.files["arquivo"]
    if not arquivo.filename.endswith(".zip"):
        return jsonify({"erro": "Apenas arquivos .zip são aceitos"}), 400
    nome = secure_filename(arquivo.filename)
    caminho = os.path.join(UPLOAD_DIR, nome)
    arquivo.save(caminho)
    tamanho = os.path.getsize(caminho)
    return jsonify({
        "mensagem": "Upload concluído",
        "arquivo": nome,
        "tamanho_mb": round(tamanho / 1024 / 1024, 2)
    })


@app.route("/api/arquivos", methods=["GET"])
def api_arquivos():
    arquivos = []
    for f in os.listdir(UPLOAD_DIR):
        if f.endswith(".zip"):
            caminho = os.path.join(UPLOAD_DIR, f)
            arquivos.append({
                "nome": f,
                "tamanho_mb": round(os.path.getsize(caminho) / 1024 / 1024, 2),
                "data": time.strftime("%d/%m/%Y %H:%M",
                                       time.localtime(os.path.getmtime(caminho))),
                "versao": extrair_versao(f)
            })
    arquivos.sort(key=lambda x: x["data"], reverse=True)
    return jsonify(arquivos)


@app.route("/api/arquivos/<nome>", methods=["DELETE"])
def api_deletar_arquivo(nome):
    caminho = os.path.join(UPLOAD_DIR, secure_filename(nome))
    if os.path.exists(caminho):
        os.remove(caminho)
        return jsonify({"mensagem": f"{nome} removido"})
    return jsonify({"erro": "Arquivo não encontrado"}), 404


@app.route("/api/arquivos/limpar", methods=["DELETE"])
def api_limpar_arquivos():
    removidos = 0
    for f in os.listdir(UPLOAD_DIR):
        if f.endswith(".zip"):
            os.remove(os.path.join(UPLOAD_DIR, f))
            removidos += 1
    return jsonify({"mensagem": f"{removidos} arquivo(s) removido(s)"})


@app.route("/api/upload_agente", methods=["POST"])
def api_upload_agente():
    """Recebe o novo agente.exe e salva no servidor."""
    if "arquivo" not in request.files:
        return jsonify({"erro": "Nenhum arquivo enviado"}), 400
    arquivo = request.files["arquivo"]
    if not arquivo.filename.endswith(".exe"):
        return jsonify({"erro": "Apenas arquivos .exe sao aceitos"}), 400
    caminho = os.path.join(UPLOAD_DIR, "agente.exe")
    arquivo.save(caminho)
    tamanho = os.path.getsize(caminho)
    return jsonify({
        "mensagem": "Upload do agente concluido",
        "tamanho_mb": round(tamanho / 1024 / 1024, 2)
    })


@app.route("/api/versao_agente", methods=["GET"])
def api_versao_agente():
    """Verifica se existe agente.exe disponivel para distribuicao."""
    caminho = os.path.join(UPLOAD_DIR, "agente.exe")
    if os.path.exists(caminho):
        return jsonify({
            "disponivel": True,
            "tamanho_mb": round(os.path.getsize(caminho) / 1024 / 1024, 2),
            "data": time.strftime("%d/%m/%Y %H:%M",
                                   time.localtime(os.path.getmtime(caminho)))
        })
    return jsonify({"disponivel": False})


@app.route("/api/atualizar_agente", methods=["POST"])
def api_atualizar_agente():
    """Envia novo agente.exe para PDVs selecionados."""
    dados = request.json
    loja_id = dados.get("loja_id")
    pdv_ids = dados.get("pdv_ids", [])

    caminho_exe = os.path.join(UPLOAD_DIR, "agente.exe")
    if not os.path.exists(caminho_exe):
        return jsonify({"erro": "Nenhum agente.exe disponivel. Faca upload primeiro."}), 404

    loja = next((l for l in get_lojas() if l["id"] == loja_id), None)
    if not loja:
        return jsonify({"erro": "Loja nao encontrada"}), 404

    pdvs_alvo = loja["pdvs"] if pdv_ids == "todos" else \
        [p for p in loja["pdvs"] if p["id"] in pdv_ids]

    if not pdvs_alvo:
        return jsonify({"erro": "Nenhum PDV selecionado"}), 400

    resultados = enviar_agente_para_pdvs(caminho_exe, pdvs_alvo)
    return jsonify({"resultados": resultados})


@app.route("/api/atualizar", methods=["POST"])
def api_atualizar():
    dados = request.json
    loja_id = dados.get("loja_id")
    pdv_ids = dados.get("pdv_ids", [])
    arquivo = dados.get("arquivo")

    if not loja_id or not arquivo:
        return jsonify({"erro": "loja_id e arquivo são obrigatórios"}), 400

    if pdv_ids == "todos" or len(pdv_ids) != 1:
        return jsonify({"erro": "Selecione exatamente um PDV por vez para atualizar."}), 400

    caminho_zip = os.path.join(UPLOAD_DIR, arquivo)
    if not os.path.exists(caminho_zip):
        return jsonify({"erro": f"Arquivo {arquivo} não encontrado"}), 404

    loja = next((l for l in get_lojas() if l["id"] == loja_id), None)
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

    iniciar_envio_zip(loja_id, pdv, caminho_zip)

    return jsonify({
        "mensagem": f"Atualização iniciada para {pdv['id']}",
        "pdvs": [pdv["id"]]
    })


@app.route("/api/status/<loja_id>", methods=["GET"])
def api_status_loja(loja_id):
    return jsonify(get_atualizacoes_loja(loja_id))


@app.route("/api/status_stream/<loja_id>")
def api_status_stream(loja_id):
    def gerar():
        ultimo = None
        while True:
            atual = json.dumps(get_atualizacoes_loja(loja_id))
            if atual != ultimo:
                ultimo = atual
                yield f"data: {atual}\n\n"
            time.sleep(1)
    return Response(gerar(), mimetype="text/event-stream",
                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ──────────────────────────────────────────────
# VERIFICACAO DE REPLICACAO
# ──────────────────────────────────────────────
@app.route("/api/replicacao/verificar", methods=["POST"])
def api_replicacao_verificar():
    dados = request.json or {}
    loja_id = dados.get("loja_id")
    pdv_ids = dados.get("pdv_ids", [])

    loja = next((l for l in get_lojas() if l["id"] == loja_id), None)
    if not loja:
        return jsonify({"erro": "Loja nao encontrada"}), 404

    pdvs_alvo = loja["pdvs"] if pdv_ids == "todos" else \
        [p for p in loja["pdvs"] if p["id"] in pdv_ids]
    if not pdvs_alvo:
        return jsonify({"erro": "Nenhum PDV selecionado"}), 400

    replication.iniciar_verificacao_lote(loja_id, pdvs_alvo, tipo="manual")

    return jsonify({
        "mensagem": f"Verificacao de replicacao iniciada para {len(pdvs_alvo)} PDV(s)",
        "pdvs": [p["id"] for p in pdvs_alvo]
    })


@app.route("/api/replicacao/status/<loja_id>/<pdv_id>", methods=["GET"])
def api_replicacao_status(loja_id, pdv_id):
    return jsonify(replication.get_estado(loja_id, pdv_id))


@app.route("/api/replicacao/config", methods=["GET"])
def api_replicacao_config_get():
    return jsonify(replication.carregar_config_auto())


@app.route("/api/replicacao/config", methods=["POST"])
def api_replicacao_config_set():
    dados = request.json or {}
    alteracoes = {k: dados[k] for k in ("habilitado", "intervalo_minutos", "pdvs") if k in dados}
    return jsonify(replication.salvar_config_auto(alteracoes))


@app.route("/api/replicacao/historico", methods=["GET"])
def api_replicacao_historico():
    return jsonify(replication.obter_historico())


@app.route("/replicacao/detalhe/<loja_id>/<pdv_id>/<colecao>")
def replicacao_detalhe(loja_id, pdv_id, colecao):
    """Pagina em aba separada com o conteudo completo dos documentos
    divergentes de uma colecao (consome o mesmo /api/replicacao/status)."""
    return render_template(
        "replicacao_detalhe.html", loja_id=loja_id, pdv_id=pdv_id, colecao=colecao
    )


# ──────────────────────────────────────────────
# BANCO DE DADOS DO ERP (PostgreSQL) — Configuracoes
# ──────────────────────────────────────────────
@app.route("/api/erp_db/config", methods=["GET"])
def api_erp_db_config_get():
    return jsonify(erp_db.carregar_config())


@app.route("/api/erp_db/config", methods=["POST"])
def api_erp_db_config_set():
    dados = request.json or {}
    return jsonify(erp_db.salvar_config(dados))


@app.route("/api/erp_db/status", methods=["GET"])
def api_erp_db_status():
    return jsonify(erp_db.testar_conexao())


@app.route("/api/erp_db/pdvs_ativos", methods=["GET"])
def api_erp_db_pdvs_ativos():
    return jsonify(erp_db.listar_pdvs_ativos())


# ──────────────────────────────────────────────
# INTEGRADOR VR — Configuracoes
# ──────────────────────────────────────────────
@app.route("/api/integrador/config", methods=["GET"])
def api_integrador_config_get():
    return jsonify(integrador.carregar_config())


@app.route("/api/integrador/config", methods=["POST"])
def api_integrador_config_set():
    dados = request.json or {}
    return jsonify(integrador.salvar_config(dados))


@app.route("/api/integrador/status", methods=["GET"])
def api_integrador_status():
    return jsonify(integrador.testar_status())
