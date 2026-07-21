"""Rotas de verificacao de replicacao (Mongo da integradora x Mongo do PDV)
-- extraido de app.py (Fase 4, divisao por dominio)."""
from flask import Blueprint, jsonify, render_template, request

from pdv_server import replication
from pdv_server.auth.routes import exigir_permissao
from pdv_server.discovery import get_lojas
from pdv_server.rotas_comuns import com_rede

replicacao_bp = Blueprint("replicacao", __name__)


@replicacao_bp.route("/api/<int:rede_id>/replicacao/tabelas", methods=["GET"])
@com_rede
def api_replicacao_tabelas(contexto):
    return jsonify(replication.listar_colecoes())


@replicacao_bp.route("/api/<int:rede_id>/replicacao/verificar", methods=["POST"])
@com_rede
@exigir_permissao("pode_replic_verificar")
def api_replicacao_verificar(contexto):
    dados = request.json or {}
    loja_id = dados.get("loja_id")
    pdv_ids = dados.get("pdv_ids", [])
    tabelas = dados.get("tabelas") or None  # None = todas

    loja = next((l for l in get_lojas(contexto) if l["id"] == loja_id), None)
    if not loja:
        return jsonify({"erro": "Loja nao encontrada"}), 404

    pdvs_alvo = loja["pdvs"] if pdv_ids == "todos" else \
        [p for p in loja["pdvs"] if p["id"] in pdv_ids]
    if not pdvs_alvo:
        return jsonify({"erro": "Nenhum PDV selecionado"}), 400

    colecoes_validas = replication.listar_colecoes()
    colecoes_filtro = [t for t in tabelas if t in colecoes_validas] if tabelas else None

    replication.iniciar_verificacao_lote(contexto, loja_id, pdvs_alvo, tipo="manual", colecoes_filtro=colecoes_filtro)

    return jsonify({
        "mensagem": f"Verificacao de replicacao iniciada para {len(pdvs_alvo)} PDV(s)",
        "pdvs": [p["id"] for p in pdvs_alvo]
    })


@replicacao_bp.route("/api/<int:rede_id>/replicacao/status/<loja_id>/<pdv_id>", methods=["GET"])
@com_rede
def api_replicacao_status(contexto, loja_id, pdv_id):
    return jsonify(replication.get_estado(contexto.rede_id, loja_id, pdv_id))


@replicacao_bp.route("/api/<int:rede_id>/replicacao/config", methods=["GET"])
@com_rede
def api_replicacao_config_get(contexto):
    return jsonify(replication.carregar_config_auto(contexto))


@replicacao_bp.route("/api/<int:rede_id>/replicacao/config", methods=["POST"])
@com_rede
@exigir_permissao("pode_replic_config")
def api_replicacao_config_set(contexto):
    dados = request.json or {}
    alteracoes = {k: dados[k] for k in ("habilitado", "intervalo_minutos", "pdvs") if k in dados}
    return jsonify(replication.salvar_config_auto(contexto, alteracoes))


@replicacao_bp.route("/api/<int:rede_id>/replicacao/historico", methods=["GET"])
@com_rede
def api_replicacao_historico(contexto):
    return jsonify(replication.obter_historico(contexto))


@replicacao_bp.route("/r/<int:rede_id>/replicacao/detalhe/<loja_id>/<pdv_id>/<colecao>")
@com_rede
def replicacao_detalhe(contexto, loja_id, pdv_id, colecao):
    """Pagina em aba separada com o conteudo completo dos documentos
    divergentes de uma colecao (consome o mesmo /api/.../replicacao/status)."""
    return render_template(
        "replicacao_detalhe.html", rede_id=contexto.rede_id,
        loja_id=loja_id, pdv_id=pdv_id, colecao=colecao
    )
