"""Rotas de configuracao e consulta do banco do ERP (PostgreSQL) de cada
rede -- extraido de app.py (Fase 4, divisao por dominio)."""
from flask import Blueprint, jsonify, request
from flask_login import current_user

from pdv_server import erp_db
from pdv_server.auth.audit import registrar_auditoria
from pdv_server.auth.routes import exigir_permissao
from pdv_server.rotas_comuns import com_rede, ip_cliente

erp_bp = Blueprint("erp", __name__)


@erp_bp.route("/api/<int:rede_id>/erp_db/config", methods=["GET"])
@com_rede
def api_erp_db_config_get(contexto):
    return jsonify(erp_db.carregar_config(contexto))


@erp_bp.route("/api/<int:rede_id>/erp_db/config", methods=["POST"])
@com_rede
@exigir_permissao("pode_config_banco")
def api_erp_db_config_set(contexto):
    dados = request.json or {}
    cfg = erp_db.salvar_config(contexto, dados)
    registrar_auditoria(current_user.email, "config_erp_db", detalhes=f"rede={contexto.rede_id}", ip=ip_cliente())
    return jsonify(cfg)


@erp_bp.route("/api/<int:rede_id>/erp_db/status", methods=["GET"])
@com_rede
def api_erp_db_status(contexto):
    return jsonify(erp_db.testar_conexao(contexto))


@erp_bp.route("/api/<int:rede_id>/erp_db/pdvs_ativos", methods=["GET"])
@com_rede
def api_erp_db_pdvs_ativos(contexto):
    return jsonify(erp_db.listar_pdvs_ativos(contexto))


@erp_bp.route("/api/<int:rede_id>/erp_db/pendencias_fiscais", methods=["GET"])
@com_rede
def api_erp_db_pendencias_fiscais(contexto):
    return jsonify(erp_db.pendencias_fiscais(contexto))


@erp_bp.route("/api/<int:rede_id>/erp_db/lojas", methods=["GET"])
@com_rede
def api_erp_db_lojas(contexto):
    """Retorna lojas do ERP: apelido (tabela loja) + nome e CNPJ (tabela fornecedor)."""
    cfg = erp_db.carregar_config(contexto)
    if not cfg.get("host") or not cfg.get("banco"):
        return jsonify({"erro": "ERP não configurado.", "lojas": []})
    try:
        conn = erp_db._conectar(cfg, contexto.tailscale_site_id)
        try:
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = '8000'")
                # Tenta join com fornecedor; se falhar, retorna só a tabela loja
                try:
                    cur.execute("""
                        SELECT l.id, l.descricao, f.nomefantasia, f.cnpj
                        FROM loja l
                        LEFT JOIN fornecedor f ON f.id = l.id_fornecedor
                        ORDER BY l.id
                    """)
                    linhas = cur.fetchall()
                    lojas_ret = [
                        {"id": r[0], "apelido": r[1] or "", "nome": r[2] or "", "cnpj": r[3] or ""}
                        for r in linhas
                    ]
                except Exception:
                    # Fallback: so a tabela loja sem join
                    conn.rollback()
                    cur.execute("SELECT id, descricao FROM loja ORDER BY id")
                    linhas = cur.fetchall()
                    lojas_ret = [
                        {"id": r[0], "apelido": r[1] or "", "nome": r[1] or "", "cnpj": ""}
                        for r in linhas
                    ]
        finally:
            conn.close()
        return jsonify({"lojas": lojas_ret})
    except Exception as e:
        return jsonify({"erro": str(e), "lojas": []})


@erp_bp.route("/api/<int:rede_id>/erp_db/stats", methods=["GET"])
@com_rede
def api_erp_db_stats(contexto):
    """Retorna estatísticas do banco PostgreSQL do ERP."""
    cfg = erp_db.carregar_config(contexto)
    if not cfg.get("host") or not cfg.get("banco"):
        return jsonify({"erro": "ERP não configurado."})
    try:
        conn = erp_db._conectar(cfg, contexto.tailscale_site_id)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT version()")
                row = cur.fetchone()
                versao = row[0].split()[1] if row else "?"
                cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
                tamanho = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM pg_stat_activity WHERE state = 'active'")
                conexoes = cur.fetchone()[0]
        finally:
            conn.close()
        return jsonify({"versao": versao, "tamanho_bd": tamanho, "conexoes_ativas": int(conexoes)})
    except Exception as e:
        return jsonify({"erro": str(e)})
