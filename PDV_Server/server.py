"""
===============================================================
  PDV Server - Servidor de Atualização
  Roda no Service Manager Ubuntu.
  - Descobre PDVs automaticamente via MongoDB Replica Set
  - Cruza IPs com dados do banco pdv (lojas + pdvs)
  - Interface web para gerenciar atualizações
===============================================================
"""

import os
import json
import time
import threading
import requests
from flask import Flask, render_template, request, jsonify, Response
from werkzeug.utils import secure_filename
from config_server import TOKEN_SEGURANCA, PORTA_SERVIDOR, UPLOAD_DIR, MONGO_URI

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024

os.makedirs(UPLOAD_DIR, exist_ok=True)

# ──────────────────────────────────────────────
# MONGODB
# ──────────────────────────────────────────────
def get_mongo():
    from pymongo import MongoClient
    return MongoClient(MONGO_URI)

def descobrir_pdvs_via_replicaset():
    """
    Consulta o replica set do MongoDB para obter IPs online,
    depois chama /info em cada agente para cruzar com o banco pdv.
    Retorna lista de lojas com seus PDVs.
    """
    try:
        client = get_mongo()
        
        # Busca membros do replica set
        try:
            rs_status = client.admin.command("replSetGetStatus")
            membros = rs_status.get("members", [])
            ips_online = []
            for m in membros:
                name = m.get("name", "")  # formato "192.168.x.x:27017"
                estado_rs = m.get("stateStr", "")
                if estado_rs in ("PRIMARY", "SECONDARY") and ":" in name:
                    ip = name.split(":")[0]
                    ips_online.append(ip)
        except Exception:
            # Se não tiver replica set, tenta pegar conexões ativas
            ips_online = []

        db = client["pdv"]
        lojas_col = db["lojas"]
        pdvs_col  = db["pdvs"]

        # Busca todas as lojas e PDVs ativos do banco
        lojas_db = {l["_id"]: l for l in lojas_col.find({})}
        pdvs_db  = list(pdvs_col.find({"ativo": True}))

        # Para cada IP online, consulta o agente /info
        pdvs_com_ip = []
        threads = []
        resultados = {}

        def consultar_agente(ip):
            try:
                r = requests.get(
                    f"http://{ip}:5000/info",
                    timeout=3,
                    headers={"X-Agent-Token": TOKEN_SEGURANCA}
                )
                if r.status_code == 200:
                    dados = r.json()
                    if dados:
                        resultados[ip] = dados
            except Exception:
                pass

        for ip in ips_online:
            t = threading.Thread(target=consultar_agente, args=(ip,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=5)

        # Cruza IP + info agente com dados do MongoDB
        lojas_resultado = {}

        for ip, info_agente in resultados.items():
            numero_pdv = info_agente.get("numeroPdv")
            id_loja    = info_agente.get("idLoja")

            if not numero_pdv or not id_loja:
                continue

            # Busca dados completos do PDV no banco
            pdv_db = next(
                (p for p in pdvs_db if p.get("numeroPdv") == numero_pdv and p.get("idLoja") == id_loja),
                None
            )
            loja_db = lojas_db.get(id_loja)

            if not pdv_db or not loja_db:
                continue

            loja_id  = f"loja{id_loja:02d}"
            loja_nome = loja_db.get("descricao", f"Loja {id_loja}")

            if loja_id not in lojas_resultado:
                lojas_resultado[loja_id] = {
                    "id":   loja_id,
                    "nome": loja_nome,
                    "pdvs": []
                }

            lojas_resultado[loja_id]["pdvs"].append({
                "id":   f"PDV-{numero_pdv}",
                "nome": pdv_db.get("descricao", f"ECF {numero_pdv}"),
                "ip":   ip,
                "versao": pdv_db.get("versao", "")
            })

        # Ordena PDVs por numeroPdv dentro de cada loja
        for loja in lojas_resultado.values():
            loja["pdvs"].sort(key=lambda p: p["id"])

        client.close()
        return list(lojas_resultado.values())

    except Exception as e:
        print(f"Erro ao descobrir PDVs: {e}")
        return []

# Cache das lojas descobertas
_lojas_cache = []
_lojas_cache_ts = 0
_CACHE_TTL = 60  # segundos

def get_lojas():
    global _lojas_cache, _lojas_cache_ts
    agora = time.time()
    if agora - _lojas_cache_ts > _CACHE_TTL:
        _lojas_cache = descobrir_pdvs_via_replicaset()
        _lojas_cache_ts = agora
    return _lojas_cache

def invalidar_cache():
    global _lojas_cache_ts
    _lojas_cache_ts = 0

# ──────────────────────────────────────────────
# ESTADO DAS ATUALIZAÇÕES
# ──────────────────────────────────────────────
atualizacoes = {}
lock = threading.Lock()

def get_estado_pdv(loja_id, pdv_id):
    with lock:
        return atualizacoes.get(loja_id, {}).get(pdv_id, {
            "status": "aguardando", "etapa": "", "progresso": 0,
            "mensagem": "", "erro": "", "inicio": None, "fim": None
        })

def set_estado_pdv(loja_id, pdv_id, dados):
    with lock:
        if loja_id not in atualizacoes:
            atualizacoes[loja_id] = {}
        atualizacoes[loja_id][pdv_id] = dados

# ──────────────────────────────────────────────
# ROTAS
# ──────────────────────────────────────────────
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
    pdv = _encontrar_pdv(loja_id, pdv_id)
    if not pdv:
        return jsonify({"erro": "PDV não encontrado"}), 404
    try:
        r = requests.get(f"http://{pdv['ip']}:5000/ping", timeout=3)
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
            r = requests.get(f"http://{pdv['ip']}:5000/ping", timeout=3)
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

@app.route("/api/upload", methods=["POST"])
def api_upload():
    if "arquivo" not in request.files:
        return jsonify({"erro": "Nenhum arquivo enviado"}), 400
    arquivo = request.files["arquivo"]
    if not arquivo.filename.endswith(".zip"):
        return jsonify({"erro": "Apenas arquivos .zip são aceitos"}), 400
    nome    = secure_filename(arquivo.filename)
    caminho = os.path.join(UPLOAD_DIR, nome)
    arquivo.save(caminho)
    tamanho = os.path.getsize(caminho)
    return jsonify({
        "mensagem": "Upload concluído",
        "arquivo":  nome,
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
                        time.localtime(os.path.getmtime(caminho)))
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
    dados   = request.json
    loja_id = dados.get("loja_id")
    pdv_ids = dados.get("pdv_ids", [])

    caminho_exe = os.path.join(UPLOAD_DIR, "agente.exe")
    if not os.path.exists(caminho_exe):
        return jsonify({"erro": "Nenhum agente.exe disponivel. Faca upload primeiro."}), 404

    loja = next((l for l in get_lojas() if l["id"] == loja_id), None)
    if not loja:
        return jsonify({"erro": "Loja nao encontrada"}), 404

    pdvs_alvo = loja["pdvs"] if pdv_ids == "todos" else                 [p for p in loja["pdvs"] if p["id"] in pdv_ids]

    if not pdvs_alvo:
        return jsonify({"erro": "Nenhum PDV selecionado"}), 400

    resultados = {}
    def enviar(pdv):
        try:
            with open(caminho_exe, "rb") as f:
                r = requests.post(
                    f"http://{pdv['ip']}:5000/atualizar_agente",
                    files={"arquivo": ("agente.exe", f, "application/octet-stream")},
                    headers={"X-Agent-Token": TOKEN_SEGURANCA},
                    timeout=60
                )
            resultados[pdv["id"]] = {
                "ok": r.status_code == 200,
                "msg": r.json().get("mensagem", r.text)
            }
        except Exception as e:
            resultados[pdv["id"]] = {"ok": False, "msg": str(e)}

    threads = [threading.Thread(target=enviar, args=(p,), daemon=True) for p in pdvs_alvo]
    for t in threads: t.start()
    for t in threads: t.join(timeout=70)

    return jsonify({"resultados": resultados})

@app.route("/api/atualizar", methods=["POST"])
def api_atualizar():
    dados    = request.json
    loja_id  = dados.get("loja_id")
    pdv_ids  = dados.get("pdv_ids", [])
    arquivo  = dados.get("arquivo")

    if not loja_id or not arquivo:
        return jsonify({"erro": "loja_id e arquivo são obrigatórios"}), 400

    caminho_zip = os.path.join(UPLOAD_DIR, arquivo)
    if not os.path.exists(caminho_zip):
        return jsonify({"erro": f"Arquivo {arquivo} não encontrado"}), 404

    loja = next((l for l in get_lojas() if l["id"] == loja_id), None)
    if not loja:
        return jsonify({"erro": "Loja não encontrada"}), 404

    pdvs_alvo = loja["pdvs"] if pdv_ids == "todos" else \
                [p for p in loja["pdvs"] if p["id"] in pdv_ids]

    if not pdvs_alvo:
        return jsonify({"erro": "Nenhum PDV selecionado"}), 400

    for pdv in pdvs_alvo:
        set_estado_pdv(loja_id, pdv["id"], {
            "status": "enviando", "etapa": "Enviando arquivo",
            "progresso": 0, "mensagem": "Preparando envio...",
            "erro": "", "inicio": time.strftime("%Y-%m-%d %H:%M:%S"), "fim": None
        })
        t = threading.Thread(target=_enviar_para_pdv,
                             args=(loja_id, pdv, caminho_zip), daemon=True)
        t.start()

    return jsonify({
        "mensagem": f"Atualização iniciada para {len(pdvs_alvo)} PDV(s)",
        "pdvs": [p["id"] for p in pdvs_alvo]
    })

@app.route("/api/status/<loja_id>", methods=["GET"])
def api_status_loja(loja_id):
    with lock:
        return jsonify(atualizacoes.get(loja_id, {}))

@app.route("/api/status_stream/<loja_id>")
def api_status_stream(loja_id):
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
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ──────────────────────────────────────────────
# ENVIO AO PDV
# ──────────────────────────────────────────────
def _encontrar_pdv(loja_id, pdv_id):
    loja = next((l for l in get_lojas() if l["id"] == loja_id), None)
    if not loja:
        return None
    return next((p for p in loja["pdvs"] if p["id"] == pdv_id), None)

def _enviar_para_pdv(loja_id, pdv, caminho_zip):
    pdv_id = pdv["id"]
    ip     = pdv["ip"]
    try:
        set_estado_pdv(loja_id, pdv_id, {
            "status": "enviando", "etapa": "Enviando arquivo",
            "progresso": 5, "mensagem": f"Enviando para {ip}...",
            "erro": "", "inicio": time.strftime("%Y-%m-%d %H:%M:%S"), "fim": None
        })
        with open(caminho_zip, "rb") as f:
            r = requests.post(
                f"http://{ip}:5000/atualizar",
                files={"arquivo": (os.path.basename(caminho_zip), f, "application/zip")},
                headers={"X-Agent-Token": TOKEN_SEGURANCA},
                timeout=120
            )
        if r.status_code != 200:
            raise Exception(f"Agente recusou: {r.text}")
        _monitorar_pdv(loja_id, pdv_id, ip)
    except requests.exceptions.ConnectionError:
        set_estado_pdv(loja_id, pdv_id, {
            "status": "error", "etapa": "Sem conexão", "progresso": 0,
            "mensagem": "", "erro": f"PDV {ip} não acessível.",
            "inicio": time.strftime("%Y-%m-%d %H:%M:%S"),
            "fim":    time.strftime("%Y-%m-%d %H:%M:%S")
        })
    except Exception as e:
        set_estado_pdv(loja_id, pdv_id, {
            "status": "error", "etapa": "Erro no envio", "progresso": 0,
            "mensagem": "", "erro": str(e),
            "inicio": time.strftime("%Y-%m-%d %H:%M:%S"),
            "fim":    time.strftime("%Y-%m-%d %H:%M:%S")
        })

def _monitorar_pdv(loja_id, pdv_id, ip):
    falhas = 0
    while True:
        try:
            r = requests.get(f"http://{ip}:5000/status", timeout=5)
            dados = r.json()
            set_estado_pdv(loja_id, pdv_id, dados)
            if dados["status"] in ("success", "error"):
                break
            falhas = 0
        except Exception:
            falhas += 1
            if falhas >= 10:
                set_estado_pdv(loja_id, pdv_id, {
                    "status": "error", "etapa": "Sem resposta", "progresso": 0,
                    "mensagem": "", "erro": "PDV parou de responder.",
                    "inicio": None, "fim": time.strftime("%Y-%m-%d %H:%M:%S")
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
    # Pré-carrega lojas em background
    threading.Thread(target=get_lojas, daemon=True).start()
    app.run(host="0.0.0.0", port=PORTA_SERVIDOR, debug=False)
