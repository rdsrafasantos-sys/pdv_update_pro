import json
import os

CONFIG_PADRAO = {"host": "", "porta": 5432, "usuario": "", "senha": "", "banco": ""}

CAMPOS_CONFIG = ("host", "porta", "usuario", "senha", "banco")


def _arquivo_config(contexto):
    return os.path.join(contexto.erp_db_dir, "config.json")


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


def _conectar(cfg, tailscale_site_id=""):
    import psycopg2
    from pdv_server.discovery import endereco_alcancavel
    host = endereco_alcancavel(cfg["host"], tailscale_site_id)
    return psycopg2.connect(
        host=host,
        port=int(cfg.get("porta") or 5432),
        user=cfg.get("usuario") or None,
        password=cfg.get("senha") or None,
        dbname=cfg["banco"],
        connect_timeout=3,
    )


def testar_conexao(contexto):
    """Tenta conectar no Postgres do ERP com timeout curto, a partir da
    configuracao salva desta rede. Nunca expoe a senha no resultado."""
    cfg = carregar_config(contexto)
    if not cfg.get("host") or not cfg.get("banco"):
        return {"online": False, "erro": "Conexao com o banco do ERP ainda nao configurada."}
    try:
        conn = _conectar(cfg, contexto.tailscale_site_id)
        conn.close()
        return {"online": True, "erro": None}
    except Exception as e:
        return {"online": False, "erro": str(e)}


def pendencias_fiscais(contexto):
    """Retorna dias sem consistência finalizada e NFC-e pendentes de transmissão."""
    cfg = carregar_config(contexto)
    if not cfg.get("host") or not cfg.get("banco"):
        return {"erro": "Conexão com o banco do ERP não configurada.",
                "consistencia": {"total": 0, "dias": []},
                "nfce": {"total": 0, "pendentes": []}}
    try:
        conn = _conectar(cfg, contexto.tailscale_site_id)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT c.data, l.descricao AS loja
                    FROM pdv.consistencia c
                    JOIN loja l ON l.id = c.id_loja
                    WHERE c.calculovendamedia = false
                      AND c.data >= CURRENT_DATE - INTERVAL '180 days'
                    ORDER BY c.data DESC, l.descricao
                """)
                dias = [{"data": str(r[0]), "loja": r[1]} for r in cur.fetchall()]

                cur.execute("""
                    SELECT v.data, l.descricao AS loja, v.ecf, v.numerocupom,
                           s.descricao AS situacao, vn.contingencia,
                           v.subtotalimpressora AS valor, vn.motivorejeicao
                    FROM pdv.vendanfce vn
                    JOIN pdv.venda v ON v.id = vn.id_venda
                    JOIN loja l ON l.id = v.id_loja
                    JOIN public.situacaonfe s ON s.id = vn.id_situacaonfce
                    WHERE vn.transmitido = false
                    ORDER BY v.data DESC, v.id_loja, v.ecf, vn.numerocupom
                    LIMIT 500
                """)
                pendentes = [
                    {"data": str(r[0]), "loja": r[1], "ecf": r[2],
                     "numerocupom": r[3], "situacao": r[4],
                     "contingencia": bool(r[5]),
                     "valor": float(r[6]) if r[6] else 0.0,
                     "motivo": r[7] or ""}
                    for r in cur.fetchall()
                ]
        finally:
            conn.close()

        return {
            "erro": None,
            "consistencia": {"total": len(dias), "dias": dias},
            "nfce": {"total": len(pendentes), "pendentes": pendentes},
        }
    except Exception as e:
        return {"erro": str(e),
                "consistencia": {"total": 0, "dias": []},
                "nfce": {"total": 0, "pendentes": []}}


def listar_pdvs_ativos(contexto):
    """Consulta no ERP os PDVs cadastrados como ativos (situacao de cadastro = 1),
    agrupados por loja. Esta lista e a "fonte da verdade" de quais PDVs deveriam
    existir em cada loja -- nao indica se o PDV esta de fato ligado/online, isso
    e cruzado depois com a verificacao de ping nos agentes."""
    cfg = carregar_config(contexto)
    if not cfg.get("host") or not cfg.get("banco"):
        return {"erro": "Conexao com o banco do ERP ainda nao configurada.", "lojas": []}
    try:
        conn = _conectar(cfg, contexto.tailscale_site_id)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT e.id_loja, l.descricao AS loja, e.ecf,
                           e.id_modelopdv, m.descricao AS modelo
                    FROM pdv.ecf e
                    INNER JOIN loja l ON l.id = e.id_loja
                    LEFT JOIN pdv.modelo m ON m.id = e.id_modelopdv
                    WHERE e.id_situacaocadastro = 1
                    ORDER BY l.descricao, e.ecf
                """)
                linhas = cur.fetchall()
        finally:
            conn.close()

        lojas = {}
        for id_loja, loja_nome, ecf, modelo_id, modelo in linhas:
            grupo = lojas.setdefault(id_loja, {"id_loja": id_loja, "loja": loja_nome, "pdvs": []})
            grupo["pdvs"].append({"ecf": ecf, "modelo_id": modelo_id, "modelo": modelo})
        return {"erro": None, "lojas": list(lojas.values())}
    except Exception as e:
        return {"erro": str(e), "lojas": []}
