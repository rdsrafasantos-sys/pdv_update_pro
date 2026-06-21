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


def testar_conexao():
    """Tenta conectar no Postgres do ERP com timeout curto, a partir da
    configuracao salva. Nunca expoe a senha no resultado."""
    cfg = carregar_config()
    if not cfg.get("host") or not cfg.get("banco"):
        return {"online": False, "erro": "Conexao com o banco do ERP ainda nao configurada."}
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=cfg["host"],
            port=int(cfg.get("porta") or 5432),
            user=cfg.get("usuario") or None,
            password=cfg.get("senha") or None,
            dbname=cfg["banco"],
            connect_timeout=5,
        )
        conn.close()
        return {"online": True, "erro": None}
    except Exception as e:
        return {"online": False, "erro": str(e)}
