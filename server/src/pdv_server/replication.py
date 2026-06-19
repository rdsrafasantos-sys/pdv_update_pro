import datetime
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

# Quantos documentos completos (nao so o _id) trazer como exemplo de cada
# categoria de divergencia, para exibir o conteudo real na tela de detalhe.
MAX_EXEMPLOS = 20

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


def _serializar_valor(v):
    """Converte tipos do BSON/Python que o json padrao nao serializa
    (ObjectId, datetime) para algo exibivel na tela de detalhe."""
    if isinstance(v, dict):
        return {str(k): _serializar_valor(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_serializar_valor(x) for x in v]
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.isoformat()
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def _comparar_colecao(col_integradora, col_pdv):
    docs_integradora = {d["_id"]: d for d in col_integradora.find({})}
    docs_pdv = {d["_id"]: d for d in col_pdv.find({})}

    ids_integradora = set(docs_integradora)
    ids_pdv = set(docs_pdv)

    faltando_ids = sorted(ids_integradora - ids_pdv, key=str)
    extras_ids = sorted(ids_pdv - ids_integradora, key=str)
    alterados_ids = sorted(
        (i for i in (ids_integradora & ids_pdv)
         if _remover_campos_ignorados(docs_integradora[i]) != _remover_campos_ignorados(docs_pdv[i])),
        key=str,
    )

    faltando = [str(i) for i in faltando_ids]
    extras = [str(i) for i in extras_ids]
    alterados = [str(i) for i in alterados_ids]

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
        "exemplos_faltando": [
            _serializar_valor(docs_integradora[i]) for i in faltando_ids[:MAX_EXEMPLOS]
        ],
        "exemplos_extras": [
            _serializar_valor(docs_pdv[i]) for i in extras_ids[:MAX_EXEMPLOS]
        ],
        "exemplos_alterados": [
            {
                "id": str(i),
                "integradora": _serializar_valor(docs_integradora[i]),
                "pdv": _serializar_valor(docs_pdv[i]),
            }
            for i in alterados_ids[:MAX_EXEMPLOS]
        ],
    }


def _comparar_colecao_com_retry(col_integradora, col_pdv, tentativas=2):
    """A replicacao do PDV pode fazer drop+reinsert da colecao durante a
    leitura (erro 'collection dropped' / code 175 QueryPlanKilled). Isso e
    uma condicao de corrida transitoria, nao uma falha real -- tenta de novo
    antes de reportar erro para essa colecao especifica."""
    from pymongo.errors import OperationFailure

    ultimo_erro = None
    for tentativa in range(tentativas):
        try:
            return _comparar_colecao(col_integradora, col_pdv)
        except OperationFailure as e:
            ultimo_erro = e
            if tentativa < tentativas - 1:
                time.sleep(2)
    return {"erro": f"Falha ao ler colecao (provavel replicacao em andamento): {ultimo_erro}"}


def comparar_pdv(pdv_ip, callback=None):
    """Compara as colecoes da integradora com as do PDV em pdv_ip.

    Conecta direto no MongoDB do PDV (porta PDV_LOCAL_MONGO_PORTA) -- exige
    rota de rede livre do Service Manager até essa porta em cada PDV.

    Se "callback" for informado, e chamado como callback(nome, resultado)
    logo apos cada colecao terminar, para a UI poder exibir o resultado
    parcial em sequencia em vez de esperar todas as colecoes.
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
            r = _comparar_colecao_com_retry(db_integradora[nome], db_pdv[nome])
            colecoes_resultado[nome] = r
            if r.get("tem_divergencia"):
                tem_divergencia_geral = True
            if callback:
                callback(nome, r)

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


def _comparar_pdv_com_progresso(loja_id, pdv_id, pdv_ip):
    """Roda comparar_pdv atualizando o estado a cada colecao concluida, para
    a UI exibir os resultados em sequencia em vez de tudo de uma vez no final."""
    inicio = time.strftime("%Y-%m-%d %H:%M:%S")
    colecoes_parciais = {}

    def ao_concluir_colecao(nome, resultado_colecao):
        colecoes_parciais[nome] = resultado_colecao
        tem_div = any(c.get("tem_divergencia") for c in colecoes_parciais.values())
        _set_estado(loja_id, pdv_id, {
            "status": "executando", "inicio": inicio, "fim": None,
            "resultado": {
                "ok": True, "tem_divergencia": tem_div,
                "colecoes": dict(colecoes_parciais),
            },
            "erro": "",
        })

    resultado = comparar_pdv(pdv_ip, callback=ao_concluir_colecao)
    _concluir_verificacao(loja_id, pdv_id, resultado)
    return resultado


def iniciar_verificacao(loja_id, pdv_id, pdv_ip):
    """Dispara a comparacao em background. Consulte get_estado() para o resultado."""
    _set_estado(loja_id, pdv_id, {
        "status": "executando", "inicio": time.strftime("%Y-%m-%d %H:%M:%S"),
        "fim": None, "resultado": None, "erro": "",
    })

    threading.Thread(
        target=_comparar_pdv_com_progresso, args=(loja_id, pdv_id, pdv_ip), daemon=True
    ).start()


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
        resultado = _comparar_pdv_com_progresso(loja_id, pdv["id"], pdv["ip"])
        ok = resultado.get("ok", False)
        tem_div = resultado.get("tem_divergencia") if ok else None
        detalhes[pdv["id"]] = {
            "loja_id": loja_id, "ok": ok, "tem_divergencia": tem_div,
            "erro": None if ok else resultado.get("erro"),
        }
        if tem_div:
            divergencia_geral = True

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
