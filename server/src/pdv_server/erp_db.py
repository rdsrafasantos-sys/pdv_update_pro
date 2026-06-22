import json
import os

from pdv_server.config import ERP_DB_DATA_DIR

os.makedirs(ERP_DB_DATA_DIR, exist_ok=True)
ARQUIVO_CONFIG = os.path.join(ERP_DB_DATA_DIR, "config.json")

CONFIG_PADRAO = {"host": "", "porta": 5432, "usuario": "", "senha": "", "banco": ""}

CAMPOS_CONFIG = ("host", "porta", "usuario", "senha", "banco")


def carregar_config():
    if not os.path.exists(ARQUIVO_CONFIG):
        return dict(CONFIG_PADRAO)
    try:
        with open(ARQUIVO_CONFIG, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return {**CONFIG_PADRAO, **cfg}
    except Exception:
        return dict(CONFIG_PADRAO)


def salvar_config(alteracoes):
    atual = carregar_config()
    atual.update({k: v for k, v in alteracoes.items() if k in CAMPOS_CONFIG})
    with open(ARQUIVO_CONFIG, "w", encoding="utf-8") as f:
        json.dump(atual, f, ensure_ascii=False)
    return atual


def _conectar(cfg):
    import psycopg2
    return psycopg2.connect(
        host=cfg["host"],
        port=int(cfg.get("porta") or 5432),
        user=cfg.get("usuario") or None,
        password=cfg.get("senha") or None,
        dbname=cfg["banco"],
        connect_timeout=5,
    )


def testar_conexao():
    """Tenta conectar no Postgres do ERP com timeout curto, a partir da
    configuracao salva. Nunca expoe a senha no resultado."""
    cfg = carregar_config()
    if not cfg.get("host") or not cfg.get("banco"):
        return {"online": False, "erro": "Conexao com o banco do ERP ainda nao configurada."}
    try:
        conn = _conectar(cfg)
        conn.close()
        return {"online": True, "erro": None}
    except Exception as e:
        return {"online": False, "erro": str(e)}


def listar_pdvs_ativos():
    """Consulta no ERP os PDVs cadastrados como ativos (situacao de cadastro = 1),
    agrupados por loja. Esta lista e a "fonte da verdade" de quais PDVs deveriam
    existir em cada loja -- nao indica se o PDV esta de fato ligado/online, isso
    e cruzado depois com a verificacao de ping nos agentes."""
    cfg = carregar_config()
    if not cfg.get("host") or not cfg.get("banco"):
        return {"erro": "Conexao com o banco do ERP ainda nao configurada.", "lojas": []}
    try:
        conn = _conectar(cfg)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT e.id_loja, l.descricao AS loja, e.ecf
                    FROM pdv.ecf e
                    INNER JOIN loja l ON l.id = e.id_loja
                    WHERE e.id_situacaocadastro = 1
                    ORDER BY l.descricao, e.ecf
                """)
                linhas = cur.fetchall()
        finally:
            conn.close()

        lojas = {}
        for id_loja, loja_nome, ecf in linhas:
            grupo = lojas.setdefault(id_loja, {"id_loja": id_loja, "loja": loja_nome, "pdvs": []})
            grupo["pdvs"].append(ecf)
        return {"erro": None, "lojas": list(lojas.values())}
    except Exception as e:
        return {"erro": str(e), "lojas": []}
