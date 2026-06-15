"""
PDV Agent v1.4 - Agente de Atualização
Roda como serviço Windows em cada PDV.
"""

import os, sys, time, shutil, zipfile, subprocess
import threading, logging, json, struct, re
from flask import Flask, request, jsonify
from waitress import serve

PORTA           = 5000
VRPDV_DIR       = r"C:\vrpdv"
VRPDV_OLD_DIR   = r"C:\vrpdv_old"
TEMP_ZIP        = r"C:\vrpdv\_update.zip"
LMDB_PATH       = r"C:\vrpdv\db\localdb"
DB_DIR          = r"C:\vrpdv\db"
DB_TEMP_DIR     = r"C:\PDVAgent\db_backup"
PROCESSOS       = ["vrcheckout", "vrpdvapi"]
LOG_FILE        = r"C:\PDVAgent\agente_pdv.log"
PROGRESSO_FILE  = r"C:\PDVAgent\progresso.json"
TOKEN_SEGURANCA = "pdv-agent-2024"

CONJUNTOS_SERVICOS = [
    ["MongoDumpRestore", "MongoFilho", "MongoStandalone"],
    ["MongoDireto", "MongoStandalone"],
]

os.makedirs(r"C:\PDVAgent", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(
            open(sys.stdout.fileno(), mode="w", encoding="utf-8",
                 errors="replace", closefd=False)
        )
    ]
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# LEITURA DO LMDB
# ──────────────────────────────────────────────
def ler_info_pdv():
    try:
        import lmdb
        env = lmdb.open(LMDB_PATH, readonly=True, lock=False, max_dbs=100)
        info = {}
        with env.begin() as txn:
            KEY_CONFIG = bytes.fromhex("1800000c00000001")
            value = txn.get(KEY_CONFIG)
            if value and len(value) >= 232:
                for p in range(100, len(value) - 12, 4):
                    v1  = struct.unpack_from("<i", value, p)[0]
                    gap = struct.unpack_from("<i", value, p + 4)[0]
                    v2  = struct.unpack_from("<i", value, p + 8)[0]
                    if 100 <= v1 <= 9999 and gap == 0 and 1 <= v2 <= 99:
                        info["numeroPdv"] = v1
                        info["idLoja"]    = v2
                        log.info(f"numeroPdv={v1} idLoja={v2} na pos {p}")
                        break
        env.close()
        if info:
            log.info(f"Info PDV lida: {info}")
        else:
            log.warning("Nao foi possivel extrair info do LMDB")
        return info if info else None
    except ImportError:
        log.warning("lmdb nao instalado")
        return None
    except Exception as e:
        log.error(f"Erro ao ler LMDB: {e}")
        return None

_info_pdv_cache = None

def get_info_pdv():
    global _info_pdv_cache
    if _info_pdv_cache is None:
        _info_pdv_cache = ler_info_pdv()
    return _info_pdv_cache

# ──────────────────────────────────────────────
# ESTADO GLOBAL
# ──────────────────────────────────────────────
estado = {
    "status": "idle", "etapa": "", "progresso": 0,
    "mensagem": "", "erro": "", "inicio": None, "fim": None
}
lock = threading.Lock()

def set_estado(status, etapa, progresso, mensagem="", erro=""):
    with lock:
        estado.update({"status": status, "etapa": etapa, "progresso": progresso,
                       "mensagem": mensagem, "erro": erro})
        if status == "updating" and progresso == 0:
            estado["inicio"] = time.strftime("%Y-%m-%d %H:%M:%S")
            estado["fim"]    = None
        if status in ("success", "error"):
            estado["fim"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(PROGRESSO_FILE, "w", encoding="utf-8") as f:
            json.dump(dict(estado), f, ensure_ascii=False)
    except Exception:
        pass
    log.info(f"[{progresso}%] {etapa} — {mensagem or erro}")

# ──────────────────────────────────────────────
# SERVIÇOS
# ──────────────────────────────────────────────
def get_status_servico(nome):
    try:
        r = subprocess.run(["sc", "query", nome], capture_output=True, text=True)
        if r.returncode == 1060:
            return "nao_existe"
        r2 = subprocess.run(["sc", "qc", nome], capture_output=True, text=True)
        if "DISABLED" in r2.stdout or "DESATIVADO" in r2.stdout:
            return "disabled"
        if "RUNNING" in r.stdout:
            return "running"
        return "stopped"
    except Exception:
        return "nao_existe"

def detectar_servicos():
    log.info("Detectando servicos...")
    for conjunto in CONJUNTOS_SERVICOS:
        existentes = [s for s in conjunto
                      if get_status_servico(s) not in ("nao_existe", "disabled")]
        if existentes:
            log.info(f"Conjunto: {existentes}")
            return existentes
    return []

def processo_rodando(nome):
    r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {nome}.exe"],
                       capture_output=True, text=True)
    return f"{nome}.exe" in r.stdout

# ──────────────────────────────────────────────
# FLASK
# ──────────────────────────────────────────────
app = Flask(__name__)

def verificar_token(req):
    return req.headers.get("X-Agent-Token", "") == TOKEN_SEGURANCA

@app.route("/ping")
def ping():
    return jsonify({"online": True, "versao": "1.4.5"})

@app.route("/info")
def info():
    return jsonify(get_info_pdv() or {})

@app.route("/status")
def status():
    with lock:
        return jsonify(dict(estado))

@app.route("/atualizar_agente", methods=["POST"])
def atualizar_agente():
    """Recebe novo agente.exe e executa auto-atualizacao via .bat independente."""
    if not verificar_token(request):
        return jsonify({"erro": "Token invalido"}), 403
    if "arquivo" not in request.files:
        return jsonify({"erro": "Nenhum arquivo enviado"}), 400
    arq = request.files["arquivo"]
    if not arq.filename.endswith(".exe"):
        return jsonify({"erro": "Apenas arquivos .exe sao aceitos"}), 400
    try:
        pasta   = r"C:\PDVAgent"
        novo    = os.path.join(pasta, "agente_novo.exe")
        atual   = os.path.join(pasta, "agente.exe")
        nssm    = os.path.join(pasta, "nssm.exe")
        bat     = os.path.join(pasta, "atualizar_agente.bat")

        arq.save(novo)
        log.info(f"Novo agente recebido: {novo}")

        # Script .bat completamente independente
        # ping -n X = pausa de X-1 segundos (truque clássico do Windows)
        linhas = [
            "@echo off",
            "ping 127.0.0.1 -n 4 > nul",
            f'"{nssm}" stop PDVAgent',
            "ping 127.0.0.1 -n 5 > nul",
            f'copy /Y "{novo}" "{atual}"',
            f'del /F /Q "{novo}"',
            f'"{nssm}" start PDVAgent',
            'schtasks /delete /tn "PDVAgentUpdate" /f',
            f'del /F /Q "{bat}"',
        ]
        with open(bat, "w", encoding="ascii") as f:
            f.write("\r\n".join(linhas))

        # Dispara via Agendador de Tarefas do Windows.
        # A tarefa roda sob o Task Scheduler (fora da arvore de processos
        # do servico), entao sobrevive ao kill tree do NSSM no stop.
        subprocess.run(
            ["schtasks", "/create", "/tn", "PDVAgentUpdate",
             "/tr", bat, "/sc", "once", "/st", "00:00",
             "/ru", "SYSTEM", "/f"],
            capture_output=True
        )
        r = subprocess.run(
            ["schtasks", "/run", "/tn", "PDVAgentUpdate"],
            capture_output=True, text=True
        )
        log.info(f"Tarefa agendada de atualizacao disparada (rc={r.returncode}).")
        return jsonify({"mensagem": "Atualizacao do agente iniciada."}), 200

    except Exception as e:
        log.error(f"Erro ao atualizar agente: {e}")
        return jsonify({"erro": str(e)}), 500

@app.route("/atualizar", methods=["POST"])
def atualizar():
    if not verificar_token(request):
        return jsonify({"erro": "Token invalido"}), 403
    with lock:
        if estado["status"] == "updating":
            return jsonify({"erro": "Ja em andamento"}), 409
    if "arquivo" not in request.files:
        return jsonify({"erro": "Sem arquivo"}), 400
    arq = request.files["arquivo"]
    if not arq.filename.endswith(".zip"):
        return jsonify({"erro": "Apenas .zip"}), 400
    try:
        os.makedirs(VRPDV_DIR, exist_ok=True)
        arq.save(TEMP_ZIP)
    except Exception as e:
        return jsonify({"erro": str(e)}), 500
    threading.Thread(target=executar_atualizacao, daemon=True).start()
    return jsonify({"mensagem": "Iniciado"}), 200

# ──────────────────────────────────────────────
# ETAPAS DE ATUALIZAÇÃO
# ──────────────────────────────────────────────
# NOTA IMPORTANTE: o agente roda como SERVICO (Session 0) e NUNCA deve
# abrir, matar ou interagir com processos de GUI (status_pdv, vrcheckout).
# Quem gerencia a tela e abre o PDV e o status_pdv.exe, que roda na
# sessao do usuario via Run key e monitora o progresso.json.

def encerrar_processos():
    set_estado("updating", "Encerrando processos", 10)
    for proc in PROCESSOS:
        if processo_rodando(proc):
            subprocess.run(["taskkill", "/F", "/IM", f"{proc}.exe"],
                           capture_output=True)
            for _ in range(10):
                time.sleep(1)
                if not processo_rodando(proc):
                    log.info(f"{proc} encerrado.")
                    break
            else:
                log.warning(f"{proc} pode nao ter encerrado.")
        else:
            log.info(f"{proc} nao estava rodando.")
    time.sleep(2)

def parar_servicos(servicos):
    set_estado("updating", "Parando servicos", 20)
    for svc in servicos:
        st = get_status_servico(svc)
        if st in ("disabled", "nao_existe", "stopped"):
            continue
        subprocess.run(["sc", "stop", svc], capture_output=True)
        for _ in range(15):
            time.sleep(1)
            if get_status_servico(svc) == "stopped":
                log.info(f"{svc} parado.")
                break
        else:
            log.warning(f"{svc} pode nao ter parado.")
    time.sleep(2)

def salvar_banco():
    """Move a pasta db para local seguro antes da atualização."""
    if os.path.exists(DB_DIR):
        if os.path.exists(DB_TEMP_DIR):
            shutil.rmtree(DB_TEMP_DIR)
        shutil.move(DB_DIR, DB_TEMP_DIR)
        log.info(f"Banco movido para: {DB_TEMP_DIR}")
    else:
        log.warning("Pasta db nao encontrada.")

def restaurar_banco():
    """Restaura a pasta db após a atualização."""
    if os.path.exists(DB_TEMP_DIR):
        if os.path.exists(DB_DIR):
            shutil.rmtree(DB_DIR)
        shutil.move(DB_TEMP_DIR, DB_DIR)
        log.info(f"Banco restaurado: {DB_DIR}")
    else:
        log.warning("Backup do banco nao encontrado!")

def fazer_backup():
    set_estado("updating", "Realizando backup", 35)
    salvar_banco()
    if os.path.exists(VRPDV_OLD_DIR):
        shutil.rmtree(VRPDV_OLD_DIR)
    if os.path.exists(VRPDV_DIR):
        shutil.copytree(VRPDV_DIR, VRPDV_OLD_DIR,
                        ignore=shutil.ignore_patterns("_update.zip"))
    log.info("Backup OK.")

def descompactar():
    set_estado("updating", "Descompactando", 55)
    with zipfile.ZipFile(TEMP_ZIP, "r") as z:
        z.extractall(VRPDV_DIR)
    os.remove(TEMP_ZIP)
    restaurar_banco()
    global _info_pdv_cache
    _info_pdv_cache = None
    log.info("Descompactacao OK.")

def iniciar_servicos(servicos):
    set_estado("updating", "Iniciando servicos", 70)
    for svc in servicos:
        st = get_status_servico(svc)
        if st in ("disabled", "nao_existe", "running"):
            continue
        log.info(f"Iniciando {svc}...")
        subprocess.run(["sc", "start", svc], capture_output=True)
        time.sleep(3)
        if get_status_servico(svc) != "running":
            raise Exception(f"{svc} nao iniciou")
        log.info(f"{svc} OK.")

def verificar_arquivos():
    """Verifica se os arquivos principais foram copiados corretamente."""
    set_estado("updating", "Verificando arquivos", 80)
    arquivos_principais = [
        os.path.join(VRPDV_DIR, "vrcheckout.exe"),
        os.path.join(VRPDV_DIR, "vrpdvapi.exe"),
    ]
    erros = []
    for arq in arquivos_principais:
        if not os.path.exists(arq):
            erros.append(f"AUSENTE: {arq}")
        elif os.path.getsize(arq) == 0:
            erros.append(f"VAZIO: {arq}")
    if erros:
        raise Exception(f"Arquivos corrompidos ou ausentes: {'; '.join(erros)}")
    log.info("Verificacao de arquivos OK.")

def garantir_processos_encerrados():
    """Garante que vrcheckout e vrpdvapi nao estao rodando antes de abrir."""
    set_estado("updating", "Verificando processos", 85)
    for proc in PROCESSOS:
        if processo_rodando(proc):
            log.warning(f"{proc} ainda rodando — forcando encerramento.")
            subprocess.run(["taskkill", "/F", "/IM", f"{proc}.exe"],
                           capture_output=True)
            for _ in range(10):
                time.sleep(1)
                if not processo_rodando(proc):
                    log.info(f"{proc} encerrado.")
                    break
            else:
                raise Exception(f"{proc} nao foi encerrado — abortando abertura do PDV.")
        else:
            log.info(f"{proc} nao esta rodando. OK.")

def iniciar_vrcheckout():
    set_estado("updating", "Aguardando para abrir PDV", 90,
               "Aguardando 10 segundos antes de abrir o PDV...")
    log.info("Aguardando 10 segundos antes de abrir o vrcheckout...")
    time.sleep(10)
    set_estado("updating", "Iniciando vrcheckout", 95)
    log.info("status_pdv.exe abrira o vrcheckout.")
    time.sleep(1)

def executar_atualizacao():
    set_estado("updating", "Iniciando", 0, "Atualizacao iniciada...")
    try:
        servicos = detectar_servicos()
        encerrar_processos()
        parar_servicos(servicos)
        fazer_backup()
        descompactar()
        verificar_arquivos()
        garantir_processos_encerrados()
        iniciar_servicos(servicos)
        iniciar_vrcheckout()
        set_estado("success", "Concluido", 100, "Atualizacao concluida!")
    except Exception as e:
        log.error(f"ERRO: {e}")
        set_estado("error", "Erro", estado["progresso"], erro=str(e))

# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
if __name__ == "__main__":
    log.info("=" * 50)
    log.info("PDV Agent v1.4.5 iniciando...")
    log.info(f"Porta: {PORTA}")
    threading.Thread(target=get_info_pdv, daemon=True).start()
    log.info("=" * 50)
    serve(app, host="0.0.0.0", port=PORTA)
