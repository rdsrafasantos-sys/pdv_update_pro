import os
import threading
import time

import requests

from pdv_server.discovery import resolver_endereco

atualizacoes = {}
lock = threading.Lock()


def get_estado_pdv(rede_id, loja_id, pdv_id):
    with lock:
        return atualizacoes.get((rede_id, loja_id), {}).get(pdv_id, {
            "status": "aguardando", "etapa": "", "progresso": 0,
            "mensagem": "", "erro": "", "inicio": None, "fim": None
        })


def set_estado_pdv(rede_id, loja_id, pdv_id, dados):
    with lock:
        chave = (rede_id, loja_id)
        if chave not in atualizacoes:
            atualizacoes[chave] = {}
        atualizacoes[chave][pdv_id] = dados


def get_atualizacoes_loja(rede_id, loja_id):
    with lock:
        return atualizacoes.get((rede_id, loja_id), {})


def iniciar_envio_zip(contexto, loja_id, pdv, caminho_zip):
    set_estado_pdv(contexto.rede_id, loja_id, pdv["id"], {
        "status": "enviando", "etapa": "Enviando arquivo",
        "progresso": 0, "mensagem": "Preparando envio...",
        "erro": "", "inicio": time.strftime("%Y-%m-%d %H:%M:%S"), "fim": None
    })
    t = threading.Thread(target=_enviar_para_pdv,
                          args=(contexto, loja_id, pdv, caminho_zip), daemon=True)
    t.start()


def enviar_agente_para_pdvs(contexto, caminho_exe, pdvs_alvo):
    resultados = {}

    def enviar(pdv):
        endereco = resolver_endereco(pdv["ip"], contexto.tailscale_site_id)
        try:
            with open(caminho_exe, "rb") as f:
                r = requests.post(
                    f"http://{endereco}:5000/atualizar_agente",
                    files={"arquivo": ("agente.exe", f, "application/octet-stream")},
                    headers={"X-Agent-Token": contexto.token},
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


def reiniciar_mongo_pdv(contexto, pdv):
    endereco = resolver_endereco(pdv["ip"], contexto.tailscale_site_id)
    try:
        r = requests.post(
            f"http://{endereco}:5000/reiniciar_mongo",
            headers={"X-Agent-Token": contexto.token},
            timeout=40
        )
        dados = r.json()
        dados["ok"] = r.status_code == 200
        return dados
    except requests.exceptions.ConnectionError:
        return {"ok": False, "erro": f"PDV {pdv['ip']} não acessível."}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


def _enviar_para_pdv(contexto, loja_id, pdv, caminho_zip):
    pdv_id = pdv["id"]
    ip = pdv["ip"]
    rede_id = contexto.rede_id
    endereco = resolver_endereco(ip, contexto.tailscale_site_id)
    try:
        set_estado_pdv(rede_id, loja_id, pdv_id, {
            "status": "enviando", "etapa": "Enviando arquivo",
            "progresso": 5, "mensagem": f"Enviando para {endereco}...",
            "erro": "", "inicio": time.strftime("%Y-%m-%d %H:%M:%S"), "fim": None
        })
        with open(caminho_zip, "rb") as f:
            r = requests.post(
                f"http://{endereco}:5000/atualizar",
                files={"arquivo": (os.path.basename(caminho_zip), f, "application/zip")},
                headers={"X-Agent-Token": contexto.token},
                timeout=120
            )
        if r.status_code != 200:
            raise Exception(f"Agente recusou: {r.text}")
        _monitorar_pdv(contexto, loja_id, pdv_id, endereco)
    except requests.exceptions.ConnectionError:
        set_estado_pdv(rede_id, loja_id, pdv_id, {
            "status": "error", "etapa": "Sem conexão", "progresso": 0,
            "mensagem": "", "erro": f"PDV {ip} não acessível (nem via 4via6 nem IP direto).",
            "inicio": time.strftime("%Y-%m-%d %H:%M:%S"),
            "fim": time.strftime("%Y-%m-%d %H:%M:%S")
        })
    except Exception as e:
        set_estado_pdv(rede_id, loja_id, pdv_id, {
            "status": "error", "etapa": "Erro no envio", "progresso": 0,
            "mensagem": "", "erro": str(e),
            "inicio": time.strftime("%Y-%m-%d %H:%M:%S"),
            "fim": time.strftime("%Y-%m-%d %H:%M:%S")
        })


def _monitorar_pdv(contexto, loja_id, pdv_id, endereco):
    falhas = 0
    while True:
        try:
            r = requests.get(
                f"http://{endereco}:5000/status",
                timeout=5
            )
            dados = r.json()
            set_estado_pdv(contexto.rede_id, loja_id, pdv_id, dados)
            if dados["status"] in ("success", "error"):
                break
            falhas = 0
        except Exception:
            falhas += 1
            if falhas >= 10:
                set_estado_pdv(contexto.rede_id, loja_id, pdv_id, {
                    "status": "error", "etapa": "Sem resposta", "progresso": 0,
                    "mensagem": "", "erro": "PDV parou de responder.",
                    "inicio": None, "fim": time.strftime("%Y-%m-%d %H:%M:%S")
                })
                break
        time.sleep(2)
