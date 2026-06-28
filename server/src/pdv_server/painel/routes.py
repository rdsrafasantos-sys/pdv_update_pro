from flask import Blueprint, jsonify, render_template, request

from pdv_server.auth.gestao import (
    alternar_ativa_rede, criar_rede, criar_unidade, editar_rede,
    editar_unidade, excluir_unidade, listar_redes, listar_unidades,
    obter_rede,
)
from pdv_server.auth.routes import exigir_super_admin

painel_bp = Blueprint("painel", __name__)


@painel_bp.route("/redes")
@exigir_super_admin
def redes():
    return render_template("redes.html")


@painel_bp.route("/unidades")
@exigir_super_admin
def unidades_pagina():
    return render_template("unidades.html")


# ── API: Unidades ────────────────────────────────────────────

@painel_bp.route("/api/unidades", methods=["GET"])
@exigir_super_admin
def api_listar_unidades():
    return jsonify(listar_unidades())


@painel_bp.route("/api/unidades", methods=["POST"])
@exigir_super_admin
def api_criar_unidade():
    dados = request.json or {}
    try:
        return jsonify(criar_unidade(dados.get("nome")))
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400


@painel_bp.route("/api/unidades/<int:unidade_id>", methods=["PUT"])
@exigir_super_admin
def api_editar_unidade(unidade_id):
    dados = request.json or {}
    try:
        return jsonify(editar_unidade(unidade_id, dados.get("nome")))
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400


@painel_bp.route("/api/unidades/<int:unidade_id>", methods=["DELETE"])
@exigir_super_admin
def api_excluir_unidade(unidade_id):
    try:
        excluir_unidade(unidade_id)
        return jsonify({"mensagem": "Unidade excluida."})
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400


# ── API: Redes ───────────────────────────────────────────────

@painel_bp.route("/api/redes", methods=["GET"])
@exigir_super_admin
def api_listar_redes():
    return jsonify(listar_redes())


@painel_bp.route("/api/redes/<int:rede_id>", methods=["GET"])
@exigir_super_admin
def api_obter_rede(rede_id):
    try:
        return jsonify(obter_rede(rede_id, com_segredos=True))
    except ValueError as e:
        return jsonify({"erro": str(e)}), 404


@painel_bp.route("/api/redes", methods=["POST"])
@exigir_super_admin
def api_criar_rede():
    dados = request.json or {}
    try:
        return jsonify(criar_rede(
            nome=dados.get("nome"),
            unidade_id=dados.get("unidade_id"),
            mongo_uri=dados.get("mongo_uri"),
            token=dados.get("token"),
            tailscale_site_id=dados.get("tailscale_site_id", ""),
        ))
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400


@painel_bp.route("/api/redes/<int:rede_id>", methods=["PUT"])
@exigir_super_admin
def api_editar_rede(rede_id):
    dados = request.json or {}
    try:
        return jsonify(editar_rede(
            rede_id,
            nome=dados.get("nome"),
            unidade_id=dados.get("unidade_id"),
            mongo_uri=dados.get("mongo_uri"),
            token=dados.get("token"),
            tailscale_site_id=dados.get("tailscale_site_id"),
        ))
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400


@painel_bp.route("/api/redes/<int:rede_id>/ativa", methods=["POST"])
@exigir_super_admin
def api_alternar_rede(rede_id):
    dados = request.json or {}
    try:
        return jsonify(alternar_ativa_rede(rede_id, dados.get("ativa", True)))
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400
