import threading
import time

import requests

_CACHE_TTL = 60  # segundos

# Cache por rede -- {rede_id: {"lojas": [...], "ts": float}}
_cache = {}
_cache_lock = threading.Lock()


def resolver_endereco(ip_raw, tailscale_site_id, porta=5000, timeout_tcp=2):
    """Testa 4via6 com TCP rapido; se falhar usa o IP direto.

    Permite que o painel alcance PDVs tanto via subnet router Tailscale
    (4via6) quanto diretamente pela LAN ou IP Tailscale proprio do PDV,
    sem depender do service manager estar online.
    """
    import socket
    principal = endereco_alcancavel(ip_raw, tailscale_site_id)
    if principal == ip_raw:
        return ip_raw
    try:
        socket.create_connection((principal, porta), timeout=timeout_tcp).close()
        return principal
    except Exception:
        return ip_raw


def endereco_alcancavel(ip, tailscale_site_id=""):
    """Traduz o IP bruto do PDV (o mesmo usado no replica set do Mongo, que
    nunca muda) para o formato MagicDNS "via" exigido por um subnet router
    4via6, quando a rede tiver um Tailscale Site ID configurado. Sem isso,
    retorna o IP como esta (Tailscale instalado direto em cada maquina, sem
    rede sobreposta). Usar SEMPRE que for abrir uma conexao (HTTP ou Mongo)
    com o PDV -- para exibicao na UI, use o IP bruto."""
    if not tailscale_site_id:
        return ip
    return f"{ip.replace('.', '-')}-via-{tailscale_site_id}"


def _get_mongo(contexto):
    from pymongo import MongoClient
    return MongoClient(contexto.mongo_uri, serverSelectionTimeoutMS=3000)


def descobrir_pdvs_via_replicaset(contexto):
    """
    Consulta o replica set do MongoDB desta rede para obter IPs online,
    depois chama /info em cada agente para cruzar com o banco pdv.
    Retorna lista de lojas com seus PDVs.
    """
    try:
        client = _get_mongo(contexto)

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
            ips_online = []

        db = client["pdv"]
        lojas_col = db["lojas"]
        pdvs_col = db["pdvs"]

        lojas_db = {l["_id"]: l for l in lojas_col.find({})}
        pdvs_db = list(pdvs_col.find({"ativo": True}))

        resultados = {}
        threads = []

        def consultar_agente(ip):
            try:
                r = requests.get(
                    f"http://{endereco_alcancavel(ip, contexto.tailscale_site_id)}:5000/info",
                    timeout=3,
                    headers={"X-Agent-Token": contexto.token}
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

        lojas_resultado = {}

        for ip, info_agente in resultados.items():
            numero_pdv = info_agente.get("numeroPdv")
            id_loja = info_agente.get("idLoja")

            if not numero_pdv or not id_loja:
                continue

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

        for loja in lojas_resultado.values():
            loja["pdvs"].sort(key=lambda p: p["id"])

        client.close()
        return list(lojas_resultado.values())

    except Exception as e:
        print(f"Erro ao descobrir PDVs da rede {contexto.nome}: {e}")
        return []


def get_lojas(contexto):
    with _cache_lock:
        entrada = _cache.get(contexto.rede_id)
    agora = time.time()
    if not entrada or agora - entrada["ts"] > _CACHE_TTL:
        lojas = descobrir_pdvs_via_replicaset(contexto)
        with _cache_lock:
            _cache[contexto.rede_id] = {"lojas": lojas, "ts": agora}
        return lojas
    return entrada["lojas"]


def invalidar_cache(rede_id):
    with _cache_lock:
        if rede_id in _cache:
            _cache[rede_id]["ts"] = 0


def encontrar_pdv(contexto, loja_id, pdv_id):
    loja = next((l for l in get_lojas(contexto) if l["id"] == loja_id), None)
    if not loja:
        return None
    return next((p for p in loja["pdvs"] if p["id"] == pdv_id), None)
