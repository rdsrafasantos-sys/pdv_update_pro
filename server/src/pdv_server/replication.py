import datetime
import json
import logging
import os
import threading
import time

from pdv_server.config import PDV_LOCAL_MONGO_PORTA, REPLICACAO_DB
from pdv_server.discovery import endereco_alcancavel, get_lojas

log = logging.getLogger(__name__)

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

CONFIG_PADRAO = {
    "habilitado": False,
    "intervalo_minutos": 60,
    "pdvs": "todos",  # "todos" ou lista de {"loja_id":..., "pdv_id":...}
    "ultima_execucao": None,
}

_estado_verificacoes = {}  # (rede_id, loja_id, pdv_id) -> dict
_estado_lock = threading.Lock()
# RLock: salvar_config_auto() chama carregar_config_auto() enquanto detem o lock.
_config_lock = threading.RLock()


def _arquivo_config_auto(contexto):
    return os.path.join(contexto.replicacao_dir, "config_automatico.json")


def _arquivo_historico(contexto):
    return os.path.join(contexto.replicacao_dir, "historico.json")


# ──────────────────────────────────────────────
# ESTADO POR PDV (consultado pela UI durante o polling)
# ──────────────────────────────────────────────
def get_estado(rede_id, loja_id, pdv_id):
    with _estado_lock:
        return _estado_verificacoes.get((rede_id, loja_id, pdv_id), {"status": "idle"})


def _set_estado(rede_id, loja_id, pdv_id, dados):
    with _estado_lock:
        _estado_verificacoes[(rede_id, loja_id, pdv_id)] = dados


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


def listar_colecoes():
    """Retorna a lista de coleções verificáveis."""
    return list(COLECOES)


def comparar_pdv(contexto, pdv_ip, callback=None, colecoes_filtro=None):
    """Compara as colecoes da integradora desta rede com as do PDV em
    pdv_ip. Conecta direto no MongoDB do PDV (porta PDV_LOCAL_MONGO_PORTA)
    -- exige rota de rede livre do Service Manager até essa porta.

    Se "callback" for informado, e chamado como callback(nome, resultado)
    logo apos cada colecao terminar, para a UI poder exibir o resultado
    parcial em sequencia em vez de esperar todas as colecoes.

    colecoes_filtro: lista de nomes para verificar; None = todas.
    """
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    try:
        cliente_integradora = MongoClient(contexto.mongo_uri, serverSelectionTimeoutMS=5000)
        cliente_integradora.admin.command("ping")
    except PyMongoError as e:
        return {"ok": False, "erro": f"Sem conexao com a integradora: {e}"}

    endereco_pdv = endereco_alcancavel(pdv_ip, contexto.tailscale_site_id)
    try:
        cliente_pdv = MongoClient(
            f"mongodb://{endereco_pdv}:{PDV_LOCAL_MONGO_PORTA}",
            serverSelectionTimeoutMS=5000
        )
        cliente_pdv.admin.command("ping")
    except PyMongoError as e:
        cliente_integradora.close()
        return {"ok": False, "erro": f"Sem conexao com o PDV ({pdv_ip}:{PDV_LOCAL_MONGO_PORTA}): {e}"}

    try:
        db_integradora = cliente_integradora[REPLICACAO_DB]
        db_pdv = cliente_pdv[REPLICACAO_DB]

        alvo = [c for c in COLECOES if c in colecoes_filtro] if colecoes_filtro else COLECOES
        colecoes_resultado = {}
        tem_divergencia_geral = False
        for nome in alvo:
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


def _concluir_verificacao(rede_id, loja_id, pdv_id, resultado):
    if resultado.get("ok"):
        _set_estado(rede_id, loja_id, pdv_id, {
            "status": "concluido", "fim": time.strftime("%Y-%m-%d %H:%M:%S"),
            "resultado": resultado, "erro": "",
        })
    else:
        _set_estado(rede_id, loja_id, pdv_id, {
            "status": "erro", "fim": time.strftime("%Y-%m-%d %H:%M:%S"),
            "resultado": None, "erro": resultado.get("erro", "Erro desconhecido"),
        })


def _comparar_pdv_com_progresso(contexto, loja_id, pdv_id, pdv_ip, colecoes_filtro=None):
    """Roda comparar_pdv atualizando o estado a cada colecao concluida, para
    a UI exibir os resultados em sequencia em vez de tudo de uma vez no final."""
    inicio = time.strftime("%Y-%m-%d %H:%M:%S")
    colecoes_parciais = {}
    rede_id = contexto.rede_id

    def ao_concluir_colecao(nome, resultado_colecao):
        colecoes_parciais[nome] = resultado_colecao
        tem_div = any(c.get("tem_divergencia") for c in colecoes_parciais.values())
        _set_estado(rede_id, loja_id, pdv_id, {
            "status": "executando", "inicio": inicio, "fim": None,
            "resultado": {
                "ok": True, "tem_divergencia": tem_div,
                "colecoes": dict(colecoes_parciais),
            },
            "erro": "",
        })

    resultado = comparar_pdv(contexto, pdv_ip, callback=ao_concluir_colecao, colecoes_filtro=colecoes_filtro)
    _concluir_verificacao(rede_id, loja_id, pdv_id, resultado)
    return resultado


def iniciar_verificacao_lote(contexto, loja_id, pdvs, tipo="manual", colecoes_filtro=None):
    """Dispara a comparacao para varios PDVs selecionados na UI, em sequencia
    numa unica thread (assim como a verificacao automatica), registrando um
    unico item consolidado no historico quando todos terminarem."""
    rede_id = contexto.rede_id
    for pdv in pdvs:
        _set_estado(rede_id, loja_id, pdv["id"], {
            "status": "executando", "inicio": time.strftime("%Y-%m-%d %H:%M:%S"),
            "fim": None, "resultado": None, "erro": "",
        })

    def trabalhar():
        detalhes = {}
        divergencia_geral = False
        for pdv in pdvs:
            resultado = _comparar_pdv_com_progresso(contexto, loja_id, pdv["id"], pdv["ip"], colecoes_filtro=colecoes_filtro)
            ok = resultado.get("ok", False)
            tem_div = resultado.get("tem_divergencia") if ok else None
            detalhes[pdv["id"]] = {
                "loja_id": loja_id, "ok": ok, "tem_divergencia": tem_div,
                "erro": None if ok else resultado.get("erro"),
            }
            if tem_div:
                divergencia_geral = True

        _registrar_historico(contexto, {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "tipo": tipo,
            "tem_divergencia": divergencia_geral,
            "pdvs": detalhes,
        })

    threading.Thread(target=trabalhar, daemon=True).start()


# ──────────────────────────────────────────────
# CONFIGURACAO DA VERIFICACAO AUTOMATICA (persistida em disco, por rede)
# ──────────────────────────────────────────────
def carregar_config_auto(contexto):
    with _config_lock:
        arquivo = _arquivo_config_auto(contexto)
        if not os.path.exists(arquivo):
            return dict(CONFIG_PADRAO)
        try:
            with open(arquivo, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return {**CONFIG_PADRAO, **cfg}
        except Exception:
            return dict(CONFIG_PADRAO)


def salvar_config_auto(contexto, alteracoes):
    with _config_lock:
        atual = carregar_config_auto(contexto)
        atual.update(alteracoes)
        with open(_arquivo_config_auto(contexto), "w", encoding="utf-8") as f:
            json.dump(atual, f, ensure_ascii=False)
        return atual


def _resolver_pdvs_alvo(contexto, cfg):
    alvo = cfg.get("pdvs", "todos")
    resultado = []
    for loja in get_lojas(contexto):
        for pdv in loja["pdvs"]:
            incluido = alvo == "todos" or any(
                a.get("loja_id") == loja["id"] and a.get("pdv_id") == pdv["id"] for a in alvo
            )
            if incluido:
                resultado.append((loja["id"], pdv))
    return resultado


# ──────────────────────────────────────────────
# HISTORICO (persistido em disco, por rede -- serve de "notificacao" no painel)
# ──────────────────────────────────────────────
def obter_historico(contexto):
    arquivo = _arquivo_historico(contexto)
    if not os.path.exists(arquivo):
        return []
    try:
        with open(arquivo, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _registrar_historico(contexto, entrada):
    with _config_lock:
        historico = obter_historico(contexto)
        historico.insert(0, entrada)
        historico = historico[:MAX_HISTORICO]
        with open(_arquivo_historico(contexto), "w", encoding="utf-8") as f:
            json.dump(historico, f, ensure_ascii=False)


# ──────────────────────────────────────────────
# LOOP AUTOMATICO (percorre todas as redes ativas)
# ──────────────────────────────────────────────
def _executar_verificacao_automatica(contexto):
    cfg = carregar_config_auto(contexto)
    pdvs_alvo = _resolver_pdvs_alvo(contexto, cfg)
    detalhes = {}
    divergencia_geral = False

    for loja_id, pdv in pdvs_alvo:
        resultado = _comparar_pdv_com_progresso(contexto, loja_id, pdv["id"], pdv["ip"])
        ok = resultado.get("ok", False)
        tem_div = resultado.get("tem_divergencia") if ok else None
        detalhes[pdv["id"]] = {
            "loja_id": loja_id, "ok": ok, "tem_divergencia": tem_div,
            "erro": None if ok else resultado.get("erro"),
        }
        if tem_div:
            divergencia_geral = True

    _registrar_historico(contexto, {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tipo": "automatico",
        "tem_divergencia": divergencia_geral,
        "pdvs": detalhes,
    })
    salvar_config_auto(contexto, {"ultima_execucao": time.strftime("%Y-%m-%d %H:%M:%S")})


def loop_automatico():
    """Roda para sempre em uma thread daemon, checando a cada 30s, para
    CADA rede ativa, se e hora de disparar a verificacao automatica
    configurada pela UI daquela rede."""
    from pdv_server.auth.gestao import listar_redes
    from pdv_server.contexto import obter_contexto

    while True:
        try:
            for resumo in listar_redes():
                if not resumo["ativa"]:
                    continue
                try:
                    contexto = obter_contexto(resumo["id"])
                except Exception:
                    log.exception("loop_automatico: falha ao carregar contexto da rede %s", resumo["id"])
                    continue

                cfg = carregar_config_auto(contexto)
                if not cfg.get("habilitado"):
                    continue
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
                    _executar_verificacao_automatica(contexto)
        except Exception:
            log.exception("loop_automatico: falha inesperada no ciclo de verificacao automatica")
        time.sleep(30)
