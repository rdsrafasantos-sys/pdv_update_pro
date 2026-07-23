import hashlib
import hmac as _hmac_mod
import logging
import os
import subprocess
import threading
import time

from flask import Flask, jsonify, request

from pdv_agent import VERSION
from pdv_agent.config import PASTA_AGENTE, TOKEN_SEGURANCA, VRPDV_DIR, TEMP_ZIP

LOGS_DIR = os.path.join(VRPDV_DIR, "logs")
from pdv_agent.lmdb_reader import get_info_pdv
from pdv_agent.service_control import detectar_servicos, reiniciar_servico
from pdv_agent.update_flow import estado, executar_atualizacao, lock
from pdv_agent.vrpdv_version import ler_versao_vrpdv

log = logging.getLogger("pdv_agent")

app = Flask(__name__)


def verificar_token(req):
    return _hmac_mod.compare_digest(req.headers.get("X-Agent-Token", ""), TOKEN_SEGURANCA)


def _verificar_hmac(dados: bytes, hmac_recebido: str) -> bool:
    if not hmac_recebido:
        return False
    hmac_calc = _hmac_mod.new(TOKEN_SEGURANCA.encode(), dados, hashlib.sha256).hexdigest()
    return _hmac_mod.compare_digest(hmac_calc, hmac_recebido)


@app.route("/ping")
def ping():
    ts_ip = None
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=3
        )
        ts_ip = result.stdout.strip() or None
    except Exception:
        pass
    return jsonify({"online": True, "versao": VERSION, "tailscale_ip": ts_ip})


@app.route("/atualizar_status_pdv", methods=["POST"])
def atualizar_status_pdv():
    """Recebe novo status_pdv.exe, substitui e relança na sessao do usuario."""
    if not verificar_token(request):
        return jsonify({"erro": "Token invalido"}), 403
    if "arquivo" not in request.files:
        return jsonify({"erro": "Nenhum arquivo enviado"}), 400
    arq = request.files["arquivo"]
    try:
        dados = arq.read()
        if not _verificar_hmac(dados, request.headers.get("X-File-Hmac", "")):
            log.warning("status_pdv.exe rejeitado — HMAC invalido")
            return jsonify({"erro": "Assinatura do arquivo invalida — atualizacao recusada"}), 403
        atual = os.path.join(PASTA_AGENTE, "status_pdv.exe")
        novo = os.path.join(PASTA_AGENTE, "status_pdv_novo.exe")
        with open(novo, "wb") as f:
            f.write(dados)
        # Encerra instancia atual
        subprocess.run(["taskkill", "/F", "/IM", "status_pdv.exe"],
                       capture_output=True)
        time.sleep(1)
        os.replace(novo, atual)
        log.info("status_pdv.exe atualizado — relancando na sessao do usuario.")
        from pdv_agent.update_flow import _iniciar_na_sessao_usuario
        _iniciar_na_sessao_usuario(atual)
        return jsonify({"mensagem": "status_pdv.exe atualizado e iniciado."})
    except Exception as e:
        log.error(f"Erro ao atualizar status_pdv.exe: {e}")
        return jsonify({"erro": str(e)}), 500


@app.route("/sysinfo")
def sysinfo():
    try:
        import psutil
        mem = psutil.virtual_memory()
        drive = os.environ.get("SystemDrive", "C:\\") + "\\"
        disk = psutil.disk_usage(drive)
        return jsonify({
            "cpu_pct": psutil.cpu_percent(interval=0.3),
            "mem_total_mb": mem.total // 1048576,
            "mem_usado_mb": (mem.total - mem.available) // 1048576,
            "mem_pct": mem.percent,
            "disco_total_gb": round(disk.total / 1073741824, 1),
            "disco_usado_gb": round(disk.used / 1073741824, 1),
            "disco_pct": disk.percent,
            "uptime_seg": int(time.time() - psutil.boot_time()),
        })
    except Exception as e:
        return jsonify({"erro": str(e)})


@app.route("/info")
def info():
    dados = dict(get_info_pdv() or {})
    versao_vrpdv = ler_versao_vrpdv()
    if versao_vrpdv:
        dados["versao_vrpdv"] = versao_vrpdv
    return jsonify(dados)


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
        pasta = PASTA_AGENTE
        novo = os.path.join(pasta, "agente_novo.exe")
        atual = os.path.join(pasta, "agente.exe")
        nssm = os.path.join(pasta, "nssm.exe")
        bat = os.path.join(pasta, "atualizar_agente.bat")

        dados = arq.read()
        if not _verificar_hmac(dados, request.headers.get("X-File-Hmac", "")):
            log.warning("agente.exe rejeitado — HMAC invalido")
            return jsonify({"erro": "Assinatura do arquivo invalida — atualizacao recusada"}), 403
        with open(novo, "wb") as f:
            f.write(dados)
        log.info(f"Novo agente recebido e verificado: {novo}")

        # Detecta servicos Mongo agora, enquanto o agente ainda esta rodando,
        # para incluir o restart deles no BAT — garante que sobem mesmo se o
        # novo agente demorar a iniciar ou falhar na verificacao de startup.
        from pdv_agent.service_control import detectar_servicos
        servicos_mongo = detectar_servicos()
        linhas_mongo = [f'sc.exe start "{s}" 2>nul' for s in servicos_mongo]

        # Script .bat completamente independente (roda via Task Scheduler / SYSTEM)
        # Usa sc.exe (sempre disponivel) em vez de nssm para stop/start.
        # Loop :aguarda garante que agente.exe esta realmente morto antes de copiar.
        linhas = [
            "@echo off",
            "ping 127.0.0.1 -n 4 > nul",
            "sc.exe stop PDVAgent",
            "ping 127.0.0.1 -n 4 > nul",
            ":aguarda",
            'tasklist /FI "IMAGENAME eq agente.exe" /FO CSV 2>nul | find /I "agente.exe" >nul',
            "if not errorlevel 1 (ping 127.0.0.1 -n 3 > nul & goto aguarda)",
            f'copy /Y "{novo}" "{atual}"',
            f'del /F /Q "{novo}"',
            "sc.exe start PDVAgent",
            # Aguarda o agente subir antes de iniciar os servicos Mongo
            "ping 127.0.0.1 -n 5 > nul",
        ] + linhas_mongo + [
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


@app.route("/reiniciar_mongo", methods=["POST"])
def reiniciar_mongo():
    """Reinicia o(s) servico(s) Mongo local(is) deste PDV (ex: MongoFilho),
    sem afetar o restante do fluxo de atualizacao."""
    if not verificar_token(request):
        return jsonify({"erro": "Token invalido"}), 403
    with lock:
        if estado["status"] == "updating":
            return jsonify({"erro": "Atualizacao em andamento, aguarde terminar"}), 409
    servicos = detectar_servicos()
    if not servicos:
        return jsonify({"erro": "Nenhum servico Mongo detectado neste PDV"}), 404
    resultado = {}
    for svc in servicos:
        log.info(f"Reiniciando servico {svc} (solicitado remotamente)...")
        resultado[svc] = reiniciar_servico(svc)
    falhou = [s for s, st in resultado.items() if st != "running"]
    if falhou:
        return jsonify({
            "erro": f"Falha ao reiniciar: {', '.join(falhou)}",
            "status": resultado
        }), 500
    return jsonify({
        "mensagem": "Servico(s) Mongo reiniciado(s) com sucesso",
        "status": resultado
    }), 200


@app.route("/logs")
def listar_logs():
    """Lista os arquivos de log em C:\\VRPdv\\logs, mais recentes primeiro.

    Aceita filtro opcional por data de modificacao via query string:
    ?desde=YYYY-MM-DD&ate=YYYY-MM-DD (ambos inclusivos, comparando so a data).
    """
    if not verificar_token(request):
        return jsonify({"erro": "Token invalido"}), 403
    desde = request.args.get("desde") or None
    ate = request.args.get("ate") or None
    try:
        if not os.path.isdir(LOGS_DIR):
            return jsonify({"arquivos": []})
        arquivos = []
        for nome in os.listdir(LOGS_DIR):
            caminho = os.path.join(LOGS_DIR, nome)
            if os.path.isfile(caminho):
                st = os.stat(caminho)
                data_mod = time.strftime("%Y-%m-%d", time.localtime(st.st_mtime))
                if desde and data_mod < desde:
                    continue
                if ate and data_mod > ate:
                    continue
                arquivos.append({
                    "nome": nome,
                    "tamanho": st.st_size,
                    "modificado": f"{data_mod} {time.strftime('%H:%M:%S', time.localtime(st.st_mtime))}",
                })
        arquivos.sort(key=lambda a: a["modificado"], reverse=True)
        return jsonify({"arquivos": arquivos})
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


@app.route("/logs/<path:nome>")
def baixar_log(nome):
    """Retorna o conteudo de um arquivo especifico de C:\\VRPdv\\logs."""
    if not verificar_token(request):
        return jsonify({"erro": "Token invalido"}), 403
    nome_seguro = os.path.basename(nome)
    caminho = os.path.join(LOGS_DIR, nome_seguro)
    if not os.path.isfile(caminho):
        return jsonify({"erro": "Arquivo nao encontrado"}), 404
    from flask import send_file
    return send_file(caminho, as_attachment=True, download_name=nome_seguro)


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
        dados = arq.read()
        if not _verificar_hmac(dados, request.headers.get("X-File-Hmac", "")):
            log.warning("ZIP rejeitado — HMAC invalido")
            return jsonify({"erro": "Assinatura do arquivo invalida — atualizacao recusada"}), 403
        os.makedirs(VRPDV_DIR, exist_ok=True)
        with open(TEMP_ZIP, "wb") as f:
            f.write(dados)
    except Exception as e:
        return jsonify({"erro": str(e)}), 500
    threading.Thread(target=executar_atualizacao, daemon=True).start()
    return jsonify({"mensagem": "Iniciado"}), 200
