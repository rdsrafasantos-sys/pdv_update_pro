import json
import os
import socket
from datetime import datetime, timezone

from pdv_server.config import REPLICACAO_DB
from pdv_server.replication import COLECOES

CONFIG_PADRAO = {"ip": "", "porta": 0, "mongo_ip": "", "mongo_porta": 27016}
CAMPOS_CONFIG = ("ip", "porta", "mongo_ip", "mongo_porta")

# Se nenhuma colecao monitorada recebeu um documento novo nas ultimas N horas,
# trata como sinal de que a replicacao do integrador pode estar parada (mesmo
# que o processo e o Mongo estejam online).
HORAS_LIMITE_SEM_ATIVIDADE = 24


def _arquivo_config(contexto):
    return os.path.join(contexto.integrador_dir, "config.json")


def carregar_config(contexto):
    arquivo = _arquivo_config(contexto)
    if not os.path.exists(arquivo):
        return dict(CONFIG_PADRAO)
    try:
        with open(arquivo, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return {**CONFIG_PADRAO, **cfg}
    except Exception:
        return dict(CONFIG_PADRAO)


def salvar_config(contexto, alteracoes):
    atual = carregar_config(contexto)
    atual.update({k: v for k, v in alteracoes.items() if k in CAMPOS_CONFIG})
    with open(_arquivo_config(contexto), "w", encoding="utf-8") as f:
        json.dump(atual, f, ensure_ascii=False)
    return atual


def _porta_aberta(ip, porta, timeout=3):
    try:
        with socket.create_connection((ip, int(porta)), timeout=timeout):
            return True
    except Exception:
        return False


def testar_status(contexto):
    """Verifica se o integrador desta rede esta de fato funcionando: nao
    basta a porta responder (processo pode estar startado e travado) --
    confere tambem se as colecoes que ele alimenta no MongoDB tem dados e
    seguem recebendo insercoes recentes (usa o timestamp embutido no
    ObjectId)."""
    cfg = carregar_config(contexto)
    if not cfg.get("ip") or not cfg.get("porta") or not cfg.get("mongo_ip"):
        return {
            "status": "nao_configurado",
            "erro": "Integrador ainda nao configurado.",
            "processo_online": None,
            "mongo_online": None,
            "colecoes": {},
        }

    processo_online = _porta_aberta(cfg["ip"], cfg["porta"])

    if not processo_online:
        return {
            "status": "offline",
            "processo_online": False,
            "mongo_online": None,
            "erro": "Porta do integrador não respondeu.",
            "colecoes": {},
        }

    from bson import ObjectId
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    mongo_online = False
    colecoes_info = {}
    datas_geracao = []
    erro = None
    try:
        cliente = MongoClient(
            f"mongodb://{cfg['mongo_ip']}:{int(cfg.get('mongo_porta') or 27016)}",
            serverSelectionTimeoutMS=3000,
        )
        cliente.admin.command("ping")
        mongo_online = True
        db = cliente[REPLICACAO_DB]
        for nome in COLECOES:
            col = db[nome]
            total = col.estimated_document_count()
            ultimo = col.find_one(sort=[("_id", -1)])
            data_geracao = (
                ultimo["_id"].generation_time
                if ultimo and isinstance(ultimo.get("_id"), ObjectId)
                else None
            )
            if data_geracao:
                datas_geracao.append(data_geracao)
            colecoes_info[nome] = {
                "total": total,
                "ultima_insercao": data_geracao.strftime("%Y-%m-%d %H:%M:%S") if data_geracao else None,
            }
        cliente.close()
    except PyMongoError as e:
        erro = f"Sem conexao com o MongoDB do integrador: {e}"
    except Exception as e:
        erro = f"Erro ao consultar o MongoDB do integrador: {e}"

    if not processo_online:
        status = "offline"
        erro = erro or "Porta do integrador nao respondeu."
    elif not mongo_online:
        status = "erro"
    else:
        vazias = [nome for nome, info in colecoes_info.items() if info["total"] == 0]
        if vazias:
            status = "erro"
            erro = f"Colecao(oes) sem nenhum dado: {', '.join(vazias)}"
        elif not datas_geracao:
            status = "atencao"
            erro = "Nao foi possivel determinar a ultima atividade de replicacao."
        else:
            horas_sem_atividade = (
                datetime.now(timezone.utc) - max(datas_geracao)
            ).total_seconds() / 3600
            if horas_sem_atividade > HORAS_LIMITE_SEM_ATIVIDADE:
                status = "atencao"
                erro = f"Nenhuma colecao recebeu dados novos nas ultimas {int(horas_sem_atividade)}h."
            else:
                status = "ok"

    return {
        "status": status,
        "processo_online": processo_online,
        "mongo_online": mongo_online,
        "erro": erro,
        "colecoes": colecoes_info,
    }
