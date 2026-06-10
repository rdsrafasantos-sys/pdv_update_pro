"""
===============================================================
  PDV Server - Servidor de Atualização
  Roda no Service Manager Ubuntu.
  Envia atualizações para os agentes nos PDVs e
  fornece interface web para acompanhamento em tempo real.
===============================================================
"""

import os
import json
import time
import threading
import requests
from flask import Flask, render_template, request, jsonify, Response
from werkzeug.utils import secure_filename
from config_server import LOJAS, TOKEN_SEGURANCA, PORTA_SERVIDOR, UPLOAD_DIR

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500MB máximo

os.makedirs(UPLOAD_DIR, exist_ok=True)

# ──────────────────────────────────────────────
# ESTADO GLOBAL DAS ATUALIZAÇÕES
# ──────────────────────────────────────────────
# Estrutura: { "loja_id": { "pdv_id": { ...status... } } }
atualizacoes = {}
lock = threading.Lock()

def get_estado_pdv(loja_id, pdv_id):
    with lock:
        return atualizacoes.get(loja_id, {}).get(pdv_id, {
            "status": "aguardando",
            "etapa": "",
            "progresso": 0,
            "mensagem": "",
            "erro": "",
            "inicio": None,
            "fim": None
        })

def set_estado_pdv(loja_id, pdv_id, dados):
    with lock:
        if loja_id not in atualizacoes:
            atualizacoes[loja_id] = {}
        atualizacoes[loja_id][pdv_id] = dados

# ──────────────────────────────────────────────
# ROTAS DA API
# ──────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", lojas=LOJAS)

@app.route("/api/lojas", methods=["GET"])
def api_lojas():
    """Retorna a lista de lojas e PDVs configurados."""
    return jsonify(LOJAS)

@app.route("/api/ping/<loja_id>/<pdv_id>", methods=["GET"])
def api_ping(loja_id, pdv_id):
    """Verifica se um PDV está online."""
    pdv = _encontrar_pdv(loja_id, pdv_id)
    if not pdv:
        return jsonify({"erro": "PDV não encontrado"}), 404
    try:
        r = requests.get(
            f"http://{pdv['ip']}:5000/ping",
            timeout=3
        )
        return jsonify({"online": r.status_code == 200, "ip": pdv["ip"]})
    except Exception:
        return jsonify({"online": False, "ip": pdv["ip"]})

@app.route("/api/ping_loja/<loja_id>", methods=["GET"])
def api_ping_loja(loja_id):
    """Verifica o status de todos os PDVs de uma loja."""
    loja = next((l for l in LOJAS if l["id"] == loja_id), None)
    if not loja:
        return jsonify({"erro": "Loja não encontrada"}), 404

    resultados = {}
    threads = []

    def checar_pdv(pdv):
        try:
            r = requests.get(f"http://{pdv['ip']}:5000/ping", timeout=3)
            resultados[pdv["id"]] = {"online": r.status_code == 200}
        except Exception:
            resultados[pdv["id"]] = {"online": False}

    for pdv in loja["pdvs"]:
        t = threading.Thread(target=checar_pdv, args=(pdv,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=5)

    return jsonify(resultados)

@app.route("/api/upload", methods=["POST"])
def api_upload():
    """Recebe o .zip de atualização e salva no servidor."""
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
    """Lista os arquivos .zip disponíveis para atualização."""
    arquivos = []
    for f in os.listdir(UPLOAD_DIR):
        if f.endswith(".zip"):
            caminho = os.path.join(UPLOAD_DIR, f)
            arquivos.append({
                "nome": f,
                "tamanho_mb": round(os.path.getsize(caminho) / 1024 / 1024, 2),
                "data": time.strftime(
                    "%d/%m/%Y %H:%M",
                    time.localtime(os.path.getmtime(caminho))
                )
            })
    arquivos.sort(key=lambda x: x["data"], reverse=True)
    return jsonify(arquivos)

@app.route("/api/atualizar", methods=["POST"])
def api_atualizar():
    """Inicia a atualização nos PDVs selecionados."""
    dados = request.json
    loja_id    = dados.get("loja_id")
    pdv_ids    = dados.get("pdv_ids", [])  # lista ou "todos"
    arquivo    = dados.get("arquivo")

    if not loja_id or not arquivo:
        return jsonify({"erro": "loja_id e arquivo são obrigatórios"}), 400

    caminho_zip = os.path.join(UPLOAD_DIR, arquivo)
    if not os.path.exists(caminho_zip):
        return jsonify({"erro": f"Arquivo {arquivo} não encontrado"}), 404

    loja = next((l for l in LOJAS if l["id"] == loja_id), None)
    if not loja:
        return jsonify({"erro": "Loja não encontrada"}), 404

    # Seleciona PDVs
    if pdv_ids == "todos":
        pdvs_alvo = loja["pdvs"]
    else:
        pdvs_alvo = [p for p in loja["pdvs"] if p["id"] in pdv_ids]

    if not pdvs_alvo:
        return jsonify({"erro": "Nenhum PDV selecionado"}), 400

    # Inicia envio em paralelo para todos os PDVs
    for pdv in pdvs_alvo:
        set_estado_pdv(loja_id, pdv["id"], {
            "status": "enviando",
            "etapa": "Enviando arquivo",
            "progresso": 0,
            "mensagem": "Preparando envio...",
            "erro": "",
            "inicio": time.strftime("%Y-%m-%d %H:%M:%S"),
            "fim": None
        })
        t = threading.Thread(
            target=_enviar_para_pdv,
            args=(loja_id, pdv, caminho_zip),
            daemon=True
        )
        t.start()

    return jsonify({
        "mensagem": f"Atualização iniciada para {len(pdvs_alvo)} PDV(s)",
        "pdvs": [p["id"] for p in pdvs_alvo]
    })

@app.route("/api/status/<loja_id>", methods=["GET"])
def api_status_loja(loja_id):
    """Retorna o status de todos os PDVs de uma loja."""
    with lock:
        return jsonify(atualizacoes.get(loja_id, {}))

@app.route("/api/status_stream/<loja_id>")
def api_status_stream(loja_id):
    """Server-Sent Events para atualização em tempo real no browser."""
    def gerar():
        ultimo = None
        while True:
            with lock:
                atual = json.dumps(atualizacoes.get(loja_id, {}))
            if atual != ultimo:
                ultimo = atual
                yield f"data: {atual}\n\n"
            time.sleep(1)

    return Response(gerar(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})

# ──────────────────────────────────────────────
# LÓGICA DE ENVIO AO PDV
# ──────────────────────────────────────────────

def _encontrar_pdv(loja_id, pdv_id):
    loja = next((l for l in LOJAS if l["id"] == loja_id), None)
    if not loja:
        return None
    return next((p for p in loja["pdvs"] if p["id"] == pdv_id), None)

def _enviar_para_pdv(loja_id, pdv, caminho_zip):
    """Envia o .zip para o agente do PDV e monitora o progresso."""
    pdv_id = pdv["id"]
    ip     = pdv["ip"]

    try:
        # Envia o arquivo zip para o agente
        set_estado_pdv(loja_id, pdv_id, {
            "status": "enviando",
            "etapa": "Enviando arquivo",
            "progresso": 5,
            "mensagem": f"Enviando para {ip}...",
            "erro": "",
            "inicio": time.strftime("%Y-%m-%d %H:%M:%S"),
            "fim": None
        })

        with open(caminho_zip, "rb") as f:
            r = requests.post(
                f"http://{ip}:5000/atualizar",
                files={"arquivo": (os.path.basename(caminho_zip), f, "application/zip")},
                headers={"X-Agent-Token": TOKEN_SEGURANCA},
                timeout=120
            )

        if r.status_code != 200:
            raise Exception(f"Agente recusou o arquivo: {r.text}")

        # Monitora o progresso consultando o agente
        _monitorar_pdv(loja_id, pdv_id, ip)

    except requests.exceptions.ConnectionError:
        set_estado_pdv(loja_id, pdv_id, {
            "status": "error",
            "etapa": "Sem conexão",
            "progresso": 0,
            "mensagem": "",
            "erro": f"PDV {ip} não está acessível. Verifique se o agente está rodando.",
            "inicio": time.strftime("%Y-%m-%d %H:%M:%S"),
            "fim": time.strftime("%Y-%m-%d %H:%M:%S")
        })
    except Exception as e:
        set_estado_pdv(loja_id, pdv_id, {
            "status": "error",
            "etapa": "Erro no envio",
            "progresso": 0,
            "mensagem": "",
            "erro": str(e),
            "inicio": time.strftime("%Y-%m-%d %H:%M:%S"),
            "fim": time.strftime("%Y-%m-%d %H:%M:%S")
        })

def _monitorar_pdv(loja_id, pdv_id, ip):
    """Consulta o status do agente até concluir ou dar erro."""
    tentativas_falha = 0
    while True:
        try:
            r = requests.get(f"http://{ip}:5000/status", timeout=5)
            dados = r.json()
            set_estado_pdv(loja_id, pdv_id, dados)

            if dados["status"] in ("success", "error"):
                break

            tentativas_falha = 0
        except Exception:
            tentativas_falha += 1
            if tentativas_falha >= 10:
                set_estado_pdv(loja_id, pdv_id, {
                    "status": "error",
                    "etapa": "Sem resposta",
                    "progresso": 0,
                    "mensagem": "",
                    "erro": "PDV parou de responder durante a atualização.",
                    "inicio": None,
                    "fim": time.strftime("%Y-%m-%d %H:%M:%S")
                })
                break

        time.sleep(2)

# ──────────────────────────────────────────────
# INICIALIZAÇÃO
# ──────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("PDV Server iniciando...")
    print(f"Acesse: http://localhost:{PORTA_SERVIDOR}")
    print("=" * 50)
    app.run(host="0.0.0.0", port=PORTA_SERVIDOR, debug=False)
