"""
===============================================================
  PDV Agent - Agente de Atualização
  Roda como serviço Windows em cada PDV.
===============================================================
"""

import os
import sys
import time
import shutil
import zipfile
import subprocess
import threading
import json
import logging
from flask import Flask, request, jsonify, render_template_string
import pathlib
from waitress import serve

# ──────────────────────────────────────────────
# CONFIGURAÇÕES
# ──────────────────────────────────────────────
PORTA          = 5000
VRPDV_DIR      = r"C:\vrpdv"
VRPDV_OLD_DIR  = r"C:\vrpdv_old"
TEMP_ZIP       = r"C:\vrpdv\_update.zip"
PROCESSOS      = ["vrcheckout", "vrpdvapi"]
VRCHECKOUT_EXE = r"C:\vrpdv\vrcheckout.exe"
LOG_FILE       = r"C:\PDVAgent\agente_pdv.log"
TOKEN_SEGURANCA  = "pdv-agent-2024"
PROGRESSO_FILE   = r"C:\PDVAgent\progresso.json"

# Conjuntos de serviços possíveis — o agente detecta automaticamente qual usar
CONJUNTOS_SERVICOS = [
    ["MongoDumpRestore", "MongoFilho", "MongoStandalone"],  # Configuração padrão
    ["MongoDireto", "MongoStandalone"],                      # Configuração alternativa
]

# ──────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────
os.makedirs(r"C:\PDVAgent", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# ESTADO GLOBAL
# ──────────────────────────────────────────────
estado = {
    "status": "idle",
    "etapa": "",
    "progresso": 0,
    "mensagem": "",
    "erro": "",
    "inicio": None,
    "fim": None
}
lock = threading.Lock()

def set_estado(status, etapa, progresso, mensagem="", erro=""):
    with lock:
        estado["status"]    = status
        estado["etapa"]     = etapa
        estado["progresso"] = progresso
        estado["mensagem"]  = mensagem
        estado["erro"]      = erro
        if status == "updating" and progresso == 0:
            estado["inicio"] = time.strftime("%Y-%m-%d %H:%M:%S")
            estado["fim"]    = None
        if status in ("success", "error"):
            estado["fim"] = time.strftime("%Y-%m-%d %H:%M:%S")
    # Grava progresso em arquivo para o status_pdv.exe monitorar
    try:
        with open(PROGRESSO_FILE, "w", encoding="utf-8") as f:
            json.dump(dict(estado), f, ensure_ascii=False)
    except Exception as e:
        log.warning(f"Nao foi possivel gravar progresso.json: {e}")
    log.info(f"[{progresso}%] {etapa} — {mensagem or erro}")

# ──────────────────────────────────────────────
# DETECÇÃO DE SERVIÇOS
# ──────────────────────────────────────────────

def get_status_servico(nome):
    """Retorna o status de um serviço: 'running', 'stopped', 'disabled' ou 'nao_existe'."""
    try:
        r = subprocess.run(
            ["sc", "query", nome],
            capture_output=True, text=True
        )
        if "O serviço especificado não existe" in r.stdout or \
           "The specified service does not exist" in r.stdout or \
           r.returncode == 1060:
            return "nao_existe"

        r2 = subprocess.run(
            ["sc", "qc", nome],
            capture_output=True, text=True
        )
        # Verifica se está desativado
        if "DISABLED" in r2.stdout or "DESATIVADO" in r2.stdout:
            return "disabled"

        if "RUNNING" in r.stdout:
            return "running"

        return "stopped"
    except Exception:
        return "nao_existe"

def detectar_servicos():
    """Detecta qual conjunto de serviços está instalado e ativo nesta máquina."""
    log.info("Detectando conjunto de serviços instalados...")
    for conjunto in CONJUNTOS_SERVICOS:
        # Verifica se pelo menos um serviço do conjunto existe e não está desativado
        existentes = []
        for svc in conjunto:
            status = get_status_servico(svc)
            log.info(f"  Serviço {svc}: {status}")
            if status != "nao_existe" and status != "disabled":
                existentes.append(svc)

        if existentes:
            log.info(f"Conjunto detectado: {existentes}")
            return existentes

    log.warning("Nenhum conjunto de serviços detectado!")
    return []

# ──────────────────────────────────────────────
# FLASK APP
# ──────────────────────────────────────────────
app = Flask(__name__)

def verificar_token(req):
    return req.headers.get("X-Agent-Token", "") == TOKEN_SEGURANCA

@app.route("/status", methods=["GET"])
def status():
    with lock:
        return jsonify(dict(estado))

@app.route("/ping", methods=["GET"])
def ping():
    servicos = detectar_servicos()
    return jsonify({
        "online": True,
        "versao": "1.0.0",
        "servicos_detectados": servicos
    })

@app.route("/", methods=["GET"])
def pagina_status():
    """Página visual de acompanhamento da atualização."""
    html_path = pathlib.Path(sys.executable).parent / "status.html"
    if not html_path.exists():
        # Tenta na pasta do script
        html_path = pathlib.Path(__file__).parent / "status.html"
    try:
        html = html_path.read_text(encoding="utf-8")
        return render_template_string(html)
    except Exception as e:
        return f"<h2>status.html nao encontrado: {e}</h2>", 404

@app.route("/atualizar", methods=["POST"])
def atualizar():
    if not verificar_token(request):
        return jsonify({"erro": "Token inválido"}), 403
    with lock:
        if estado["status"] == "updating":
            return jsonify({"erro": "Atualização já em andamento"}), 409
    if "arquivo" not in request.files:
        return jsonify({"erro": "Nenhum arquivo enviado"}), 400
    arquivo = request.files["arquivo"]
    if not arquivo.filename.endswith(".zip"):
        return jsonify({"erro": "Apenas arquivos .zip são aceitos"}), 400
    try:
        os.makedirs(VRPDV_DIR, exist_ok=True)
        arquivo.save(TEMP_ZIP)
        log.info(f"Arquivo recebido: {TEMP_ZIP}")
    except Exception as e:
        return jsonify({"erro": f"Falha ao salvar arquivo: {e}"}), 500
    t = threading.Thread(target=executar_atualizacao, daemon=True)
    t.start()
    return jsonify({"mensagem": "Atualização iniciada"}), 200

# ──────────────────────────────────────────────
# ETAPAS DE ATUALIZAÇÃO
# ──────────────────────────────────────────────

def abrir_tela_status():
    """
    O status_pdv.exe já está rodando na inicialização do Windows.
    Ele monitora o progresso.json automaticamente.
    Esta função existe apenas para compatibilidade.
    """
    log.info("Progresso sendo gravado em progresso.json — status_pdv.exe vai detectar.")

def encerrar_processos():
    set_estado("updating", "Encerrando processos", 10, "Encerrando vrcheckout e vrpdvapi...")
    for proc in PROCESSOS:
        try:
            r = subprocess.run(
                ["taskkill", "/F", "/IM", f"{proc}.exe"],
                capture_output=True, text=True
            )
            if r.returncode == 0:
                log.info(f"Processo {proc} encerrado.")
            else:
                log.info(f"Processo {proc} não estava rodando.")
        except Exception as e:
            log.warning(f"Erro ao encerrar {proc}: {e}")

def parar_servicos(servicos):
    set_estado("updating", "Parando serviços", 20, f"Parando {len(servicos)} serviço(s)...")
    for svc in servicos:
        status = get_status_servico(svc)
        if status == "disabled":
            log.info(f"Serviço {svc} está desativado — pulando.")
            continue
        if status == "nao_existe":
            log.info(f"Serviço {svc} não existe nesta máquina — pulando.")
            continue
        if status == "stopped":
            log.info(f"Serviço {svc} já está parado.")
            continue
        try:
            subprocess.run(["sc", "stop", svc], capture_output=True, text=True)
            log.info(f"Serviço {svc} parado.")
            time.sleep(2)
        except Exception as e:
            log.warning(f"Erro ao parar {svc}: {e}")

def fazer_backup():
    set_estado("updating", "Realizando backup", 35, "Copiando vrpdv -> vrpdv_old...")
    try:
        if os.path.exists(VRPDV_OLD_DIR):
            shutil.rmtree(VRPDV_OLD_DIR)
            log.info("vrpdv_old removido.")
        if os.path.exists(VRPDV_DIR):
            shutil.copytree(VRPDV_DIR, VRPDV_OLD_DIR,
                ignore=shutil.ignore_patterns("_update.zip"))
            log.info("Backup realizado: vrpdv -> vrpdv_old")
    except Exception as e:
        raise Exception(f"Falha no backup: {e}")

def descompactar():
    set_estado("updating", "Descompactando", 55, "Extraindo arquivos do .zip...")
    try:
        with zipfile.ZipFile(TEMP_ZIP, "r") as z:
            z.extractall(VRPDV_DIR)
        os.remove(TEMP_ZIP)
        log.info("Descompactação concluída.")
    except Exception as e:
        raise Exception(f"Falha na descompactação: {e}")

def iniciar_servicos(servicos):
    set_estado("updating", "Iniciando serviços", 70, f"Iniciando {len(servicos)} serviço(s)...")
    for svc in servicos:
        status = get_status_servico(svc)
        if status == "disabled":
            log.info(f"Serviço {svc} está desativado — pulando.")
            continue
        if status == "nao_existe":
            log.info(f"Serviço {svc} não existe nesta máquina — pulando.")
            continue
        if status == "running":
            log.info(f"Serviço {svc} já está rodando.")
            continue
        log.info(f"Iniciando {svc}...")
        try:
            subprocess.run(["sc", "start", svc], capture_output=True, text=True)
            time.sleep(3)
            novo_status = get_status_servico(svc)
            if novo_status == "running":
                log.info(f"{svc} iniciado com sucesso.")
            else:
                raise Exception(f"{svc} não iniciou. Status: {novo_status}")
        except Exception as e:
            raise Exception(f"Falha ao iniciar {svc}: {e}")

def iniciar_vrcheckout():
    """
    Sinaliza conclusão no progresso.json.
    O status_pdv.exe (rodando como usuário) detecta e abre o vrcheckout.exe.
    """
    set_estado("updating", "Iniciando vrcheckout", 90, "Abrindo vrcheckout.exe...")
    log.info("Sinalizando conclusao — status_pdv.exe vai abrir o vrcheckout.")
    time.sleep(1)

def executar_atualizacao():
    set_estado("updating", "Iniciando", 0, "Atualização iniciada...")
    # Abre a tela de status no navegador automaticamente
    threading.Thread(target=abrir_tela_status, daemon=True).start()
    try:
        # Detecta serviços antes de começar
        servicos = detectar_servicos()
        log.info(f"Serviços que serão gerenciados: {servicos}")

        encerrar_processos()
        parar_servicos(servicos)
        fazer_backup()
        descompactar()
        iniciar_servicos(servicos)
        iniciar_vrcheckout()
        set_estado("success", "Concluído", 100, "Atualização concluída com sucesso!")
    except Exception as e:
        log.error(f"ERRO na atualização: {e}")
        set_estado("error", "Erro", estado["progresso"], erro=str(e))

# ──────────────────────────────────────────────
# INICIALIZAÇÃO
# ──────────────────────────────────────────────
if __name__ == "__main__":
    log.info("=" * 50)
    log.info("PDV Agent iniciando...")
    log.info(f"Porta: {PORTA}")
    log.info("=" * 50)
    serve(app, host="0.0.0.0", port=PORTA)
