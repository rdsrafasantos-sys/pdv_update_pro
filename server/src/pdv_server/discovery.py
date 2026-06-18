import threading
import time

import requests

from pdv_server.config import MONGO_URI, TOKEN_SEGURANCA

_CACHE_TTL = 60  # segundos

_lojas_cache = []
_lojas_cache_ts = 0


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
        pdvs_col = db["pdvs"]

        # Busca todas as lojas e PDVs ativos do banco
        lojas_db = {l["_id"]: l for l in lojas_col.find({})}
        pdvs_db = list(pdvs_col.find({"ativo": True}))

        # Para cada IP online, consulta o agente /info
        resultados = {}
        threads = []

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
            id_loja = info_agente.get("idLoja")

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

            loja_id = f"loja{id_loja:02d}"
            loja_nome = loja_db.get("descricao", f"Loja {id_loja}")

            if loja_id not in lojas_resultado:
                lojas_resultado[loja_id] = {
                    "id": loja_id,
                    "nome": loja_nome,
                    "pdvs": []
                }

            lojas_resultado[loja_id]["pdvs"].append({
                "id": f"PDV-{numero_pdv}",
                "nome": pdv_db.get("descricao", f"ECF {numero_pdv}"),
                "ip": ip,
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


def encontrar_pdv(loja_id, pdv_id):
    loja = next((l for l in get_lojas() if l["id"] == loja_id), None)
    if not loja:
        return None
    return next((p for p in loja["pdvs"] if p["id"] == pdv_id), None)
