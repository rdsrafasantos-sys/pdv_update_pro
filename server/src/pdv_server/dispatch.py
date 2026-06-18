import os
import threading
import time

import requests

from pdv_server.config import TOKEN_SEGURANCA
from pdv_server.discovery import encontrar_pdv, get_lojas

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


def get_atualizacoes_loja(loja_id):
    with lock:
        return atualizacoes.get(loja_id, {})


def iniciar_envio_zip(loja_id, pdv, caminho_zip):
    set_estado_pdv(loja_id, pdv["id"], {
        "status": "enviando", "etapa": "Enviando arquivo",
        "progresso": 0, "mensagem": "Preparando envio...",
        "erro": "", "inicio": time.strftime("%Y-%m-%d %H:%M:%S"), "fim": None
    })
    t = threading.Thread(target=_enviar_para_pdv,
                          args=(loja_id, pdv, caminho_zip), daemon=True)
    t.start()


def enviar_agente_para_pdvs(caminho_exe, pdvs_alvo):
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
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=70)
    return resultados


def _enviar_para_pdv(loja_id, pdv, caminho_zip):
    pdv_id = pdv["id"]
    ip = pdv["ip"]
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
            "fim": time.strftime("%Y-%m-%d %H:%M:%S")
        })
    except Exception as e:
        set_estado_pdv(loja_id, pdv_id, {
            "status": "error", "etapa": "Erro no envio", "progresso": 0,
            "mensagem": "", "erro": str(e),
            "inicio": time.strftime("%Y-%m-%d %H:%M:%S"),
            "fim": time.strftime("%Y-%m-%d %H:%M:%S")
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
