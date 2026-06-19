import json
import os
import threading
import time

from pdv_server.config import (
    MONGO_URI, PDV_LOCAL_MONGO_PORTA, REPLICACAO_DATA_DIR, REPLICACAO_DB,
)
from pdv_server.discovery import get_lojas

COLECOES = [
    "pessoas",
    "produtos",
    "produtoscodigobarras",
    "produtosimpostos",
    "promocoes",
    "promocoesconnectsimdepor",
    "promocoesdepor",
    "promocoeslevepor",
]

# Quantos _id reportar em cada categoria de divergencia (o total real continua
# disponivel em "_total" mesmo quando a lista e cortada).
MAX_IDS_REPORTADOS = 200

# Campos a ignorar na comparacao documento-a-documento (ex: metadados que a
# propria replicacao adiciona e que nao representam divergencia real).
CAMPOS_IGNORADOS_DIFF = set()

MAX_HISTORICO = 50

os.makedirs(REPLICACAO_DATA_DIR, exist_ok=True)
ARQUIVO_CONFIG_AUTO = os.path.join(REPLICACAO_DATA_DIR, "config_automatico.json")
ARQUIVO_HISTORICO = os.path.join(REPLICACAO_DATA_DIR, "historico.json")

CONFIG_PADRAO = {
    "habilitado": False,
    "intervalo_minutos": 60,
    "pdvs": "todos",  # "todos" ou lista de {"loja_id":..., "pdv_id":...}
    "ultima_execucao": None,
}

_estado_verificacoes = {}  # (loja_id, pdv_id) -> dict
_estado_lock = threading.Lock()
# RLock: salvar_config_auto() chama carregar_config_auto() enquanto detem o lock.
_config_lock = threading.RLock()


# ──────────────────────────────────────────────
# ESTADO POR PDV (consultado pela UI durante o polling)
# ──────────────────────────────────────────────
def get_estado(loja_id, pdv_id):
    with _estado_lock:
        return _estado_verificacoes.get((loja_id, pdv_id), {"status": "idle"})


def _set_estado(loja_id, pdv_id, dados):
    with _estado_lock:
        _estado_verificacoes[(loja_id, pdv_id)] = dados


# ──────────────────────────────────────────────
# COMPARACAO
# ──────────────────────────────────────────────
def _remover_campos_ignorados(doc):
    if not CAMPOS_IGNORADOS_DIFF:
        return doc
    return {k: v for k, v in doc.items() if k not in CAMPOS_IGNORADOS_DIFF}


def _comparar_colecao(col_integradora, col_pdv):
    docs_integradora = {d["_id"]: d for d in col_integradora.find({})}
    docs_pdv = {d["_id"]: d for d in col_pdv.find({})}

    ids_integradora = set(docs_integradora)
    ids_pdv = set(docs_pdv)

    faltando = sorted(str(i) for i in (ids_integradora - ids_pdv))
    extras = sorted(str(i) for i in (ids_pdv - ids_integradora))
    alterados = sorted(
        str(i) for i in (ids_integradora & ids_pdv)
        if _remover_campos_ignorados(docs_integradora[i]) != _remover_campos_ignorados(docs_pdv[i])
    )

    return {
        "total_integradora": len(ids_integradora),
        "total_pdv": len(ids_pdv),
        "faltando_no_pdv": faltando[:MAX_IDS_REPORTADOS],
        "faltando_no_pdv_total": len(faltando),
        "extras_no_pdv": extras[:MAX_IDS_REPORTADOS],
        "extras_no_pdv_total": len(extras),
        "alterados": alterados[:MAX_IDS_REPORTADOS],
        "alterados_total": len(alterados),
        "tem_divergencia": bool(faltando or extras or alterados),
    }


def comparar_pdv(pdv_ip):
    """Compara as colecoes da integradora com as do PDV em pdv_ip.

    Conecta direto no MongoDB do PDV (porta PDV_LOCAL_MONGO_PORTA) -- exige
    rota de rede livre do Service Manager até essa porta em cada PDV.
    """
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    try:
        cliente_integradora = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        cliente_integradora.admin.command("ping")
    except PyMongoError as e:
        return {"ok": False, "erro": f"Sem conexao com a integradora: {e}"}

    try:
        cliente_pdv = MongoClient(
            f"mongodb://{pdv_ip}:{PDV_LOCAL_MONGO_PORTA}", serverSelectionTimeoutMS=5000
        )
        cliente_pdv.admin.command("ping")
    except PyMongoError as e:
        cliente_integradora.close()
        return {"ok": False, "erro": f"Sem conexao com o PDV ({pdv_ip}:{PDV_LOCAL_MONGO_PORTA}): {e}"}

    try:
        db_integradora = cliente_integradora[REPLICACAO_DB]
        db_pdv = cliente_pdv[REPLICACAO_DB]

        colecoes_resultado = {}
        tem_divergencia_geral = False
        for nome in COLECOES:
            r = _comparar_colecao(db_integradora[nome], db_pdv[nome])
            colecoes_resultado[nome] = r
            if r["tem_divergencia"]:
                tem_divergencia_geral = True

        return {
            "ok": True,
            "tem_divergencia": tem_divergencia_geral,
            "colecoes": colecoes_resultado,
        }
    except PyMongoError as e:
        return {"ok": False, "erro": f"Erro ao comparar colecoes: {e}"}
    finally:
        cliente_integradora.close()
        cliente_pdv.close()


def _concluir_verificacao(loja_id, pdv_id, resultado):
    if resultado.get("ok"):
        _set_estado(loja_id, pdv_id, {
            "status": "concluido", "fim": time.strftime("%Y-%m-%d %H:%M:%S"),
            "resultado": resultado, "erro": "",
        })
    else:
        _set_estado(loja_id, pdv_id, {
            "status": "erro", "fim": time.strftime("%Y-%m-%d %H:%M:%S"),
            "resultado": None, "erro": resultado.get("erro", "Erro desconhecido"),
        })


def iniciar_verificacao(loja_id, pdv_id, pdv_ip):
    """Dispara a comparacao em background. Consulte get_estado() para o resultado."""
    _set_estado(loja_id, pdv_id, {
        "status": "executando", "inicio": time.strftime("%Y-%m-%d %H:%M:%S"),
        "fim": None, "resultado": None, "erro": "",
    })

    def rodar():
        _concluir_verificacao(loja_id, pdv_id, comparar_pdv(pdv_ip))

    threading.Thread(target=rodar, daemon=True).start()


# ──────────────────────────────────────────────
# CONFIGURACAO DA VERIFICACAO AUTOMATICA (persistida em disco)
# ──────────────────────────────────────────────
def carregar_config_auto():
    with _config_lock:
        if not os.path.exists(ARQUIVO_CONFIG_AUTO):
            return dict(CONFIG_PADRAO)
        try:
            with open(ARQUIVO_CONFIG_AUTO, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return {**CONFIG_PADRAO, **cfg}
        except Exception:
            return dict(CONFIG_PADRAO)


def salvar_config_auto(alteracoes):
    with _config_lock:
        atual = carregar_config_auto()
        atual.update(alteracoes)
        with open(ARQUIVO_CONFIG_AUTO, "w", encoding="utf-8") as f:
            json.dump(atual, f, ensure_ascii=False)
        return atual


def _resolver_pdvs_alvo(cfg):
    alvo = cfg.get("pdvs", "todos")
    resultado = []
    for loja in get_lojas():
        for pdv in loja["pdvs"]:
            incluido = alvo == "todos" or any(
                a.get("loja_id") == loja["id"] and a.get("pdv_id") == pdv["id"] for a in alvo
            )
            if incluido:
                resultado.append((loja["id"], pdv))
    return resultado


# ──────────────────────────────────────────────
# HISTORICO (persistido em disco -- serve de "notificacao" no painel)
# ──────────────────────────────────────────────
def obter_historico():
    if not os.path.exists(ARQUIVO_HISTORICO):
        return []
    try:
        with open(ARQUIVO_HISTORICO, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _registrar_historico(entrada):
    with _config_lock:
        historico = obter_historico()
        historico.insert(0, entrada)
        historico = historico[:MAX_HISTORICO]
        with open(ARQUIVO_HISTORICO, "w", encoding="utf-8") as f:
            json.dump(historico, f, ensure_ascii=False)


# ──────────────────────────────────────────────
# LOOP AUTOMATICO
# ──────────────────────────────────────────────
def _executar_verificacao_automatica():
    cfg = carregar_config_auto()
    pdvs_alvo = _resolver_pdvs_alvo(cfg)
    detalhes = {}
    divergencia_geral = False

    for loja_id, pdv in pdvs_alvo:
        resultado = comparar_pdv(pdv["ip"])
        ok = resultado.get("ok", False)
        tem_div = resultado.get("tem_divergencia") if ok else None
        detalhes[pdv["id"]] = {
            "loja_id": loja_id, "ok": ok, "tem_divergencia": tem_div,
            "erro": None if ok else resultado.get("erro"),
        }
        if tem_div:
            divergencia_geral = True
        _concluir_verificacao(loja_id, pdv["id"], resultado)

    _registrar_historico({
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tipo": "automatico",
        "tem_divergencia": divergencia_geral,
        "pdvs": detalhes,
    })
    salvar_config_auto({"ultima_execucao": time.strftime("%Y-%m-%d %H:%M:%S")})


def loop_automatico():
    """Roda para sempre em uma thread daemon, checando a cada 30s se e hora
    de disparar a verificacao automatica configurada pela UI."""
    while True:
        try:
            cfg = carregar_config_auto()
            if cfg.get("habilitado"):
                ultima = cfg.get("ultima_execucao")
                intervalo = cfg.get("intervalo_minutos", 60)
                if ultima is None:
                    deve_rodar = True
                else:
                    minutos_passados = (
                        time.time() - time.mktime(time.strptime(ultima, "%Y-%m-%d %H:%M:%S"))
                    ) / 60
                    deve_rodar = minutos_passados >= intervalo
                if deve_rodar:
                    _executar_verificacao_automatica()
        except Exception:
            pass
        time.sleep(30)
