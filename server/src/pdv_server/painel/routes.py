from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from pdv_server.auth.gestao import (
    alternar_ativa_rede, criar_perfil, criar_rede, criar_unidade,
    criar_usuario, editar_perfil, editar_rede, editar_unidade,
    editar_usuario, excluir_perfil, excluir_unidade, excluir_usuario,
    listar_perfis, listar_redes, listar_unidades, listar_usuarios,
    obter_rede, obter_usuario, redes_visiveis_para, usuario_pode_acessar_rede,
)
from pdv_server.auth.routes import exigir_permissao, exigir_super_admin

painel_bp = Blueprint("painel", __name__)


@painel_bp.route("/redes")
@login_required
def redes():
    return render_template("redes.html")


@painel_bp.route("/unidades")
@exigir_super_admin
def unidades_pagina():
    return render_template("unidades.html")


@painel_bp.route("/usuarios")
@exigir_permissao("pode_gerenciar_usuarios")
def usuarios_pagina():
    return render_template("usuarios.html")


# ── API: Unidades (gestao da VR -- so super-admin) ─────────────

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


# ── API: Redes (visivel a qualquer usuario, escopado pelo acesso dele) ──

@painel_bp.route("/api/redes", methods=["GET"])
@login_required
def api_listar_redes():
    return jsonify(redes_visiveis_para(int(current_user.id)))


@painel_bp.route("/api/redes/<int:rede_id>", methods=["GET"])
@login_required
def api_obter_rede(rede_id):
    if not usuario_pode_acessar_rede(int(current_user.id), rede_id):
        return jsonify({"erro": "Sem acesso a esta rede"}), 403
    try:
        return jsonify(obter_rede(rede_id, com_segredos=True))
    except ValueError as e:
        return jsonify({"erro": str(e)}), 404


@painel_bp.route("/api/redes", methods=["POST"])
@exigir_permissao("pode_gerenciar_redes")
def api_criar_rede():
    dados = request.json or {}
    unidade_id = dados.get("unidade_id")
    if not current_user.acesso_total and unidade_id not in current_user.unidade_ids:
        return jsonify({"erro": "Sem acesso a essa unidade"}), 403
    try:
        return jsonify(criar_rede(
            nome=dados.get("nome"),
            unidade_id=unidade_id,
            mongo_uri=dados.get("mongo_uri"),
            token=dados.get("token"),
            tailscale_site_id=dados.get("tailscale_site_id", ""),
        ))
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400


@painel_bp.route("/api/redes/<int:rede_id>", methods=["PUT"])
@exigir_permissao("pode_gerenciar_redes")
def api_editar_rede(rede_id):
    if not usuario_pode_acessar_rede(int(current_user.id), rede_id):
        return jsonify({"erro": "Sem acesso a esta rede"}), 403
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
@exigir_permissao("pode_gerenciar_redes")
def api_alternar_rede(rede_id):
    if not usuario_pode_acessar_rede(int(current_user.id), rede_id):
        return jsonify({"erro": "Sem acesso a esta rede"}), 403
    dados = request.json or {}
    try:
        return jsonify(alternar_ativa_rede(rede_id, dados.get("ativa", True)))
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400


# ── API: Perfis ──────────────────────────────────────────────

@painel_bp.route("/api/perfis", methods=["GET"])
@exigir_permissao("pode_gerenciar_usuarios")
def api_listar_perfis():
    return jsonify(listar_perfis())


@painel_bp.route("/api/perfis", methods=["POST"])
@exigir_permissao("pode_gerenciar_usuarios")
def api_criar_perfil():
    dados = request.json or {}
    try:
        return jsonify(criar_perfil(
            nome=dados.get("nome"), descricao=dados.get("descricao", ""),
            pode_gerenciar_redes=dados.get("pode_gerenciar_redes", False),
            pode_gerenciar_usuarios=dados.get("pode_gerenciar_usuarios", False),
            somente_leitura=dados.get("somente_leitura", False),
        ))
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400


@painel_bp.route("/api/perfis/<int:perfil_id>", methods=["PUT"])
@exigir_permissao("pode_gerenciar_usuarios")
def api_editar_perfil(perfil_id):
    dados = request.json or {}
    try:
        return jsonify(editar_perfil(
            perfil_id, nome=dados.get("nome"), descricao=dados.get("descricao"),
            pode_gerenciar_redes=dados.get("pode_gerenciar_redes"),
            pode_gerenciar_usuarios=dados.get("pode_gerenciar_usuarios"),
            somente_leitura=dados.get("somente_leitura"),
        ))
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400


@painel_bp.route("/api/perfis/<int:perfil_id>", methods=["DELETE"])
@exigir_permissao("pode_gerenciar_usuarios")
def api_excluir_perfil(perfil_id):
    try:
        excluir_perfil(perfil_id)
        return jsonify({"mensagem": "Perfil excluido."})
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400


# ── API: Usuarios ────────────────────────────────────────────

@painel_bp.route("/api/usuarios", methods=["GET"])
@exigir_permissao("pode_gerenciar_usuarios")
def api_listar_usuarios():
    return jsonify(listar_usuarios())


@painel_bp.route("/api/usuarios/<int:usuario_id>", methods=["GET"])
@exigir_permissao("pode_gerenciar_usuarios")
def api_obter_usuario(usuario_id):
    try:
        return jsonify(obter_usuario(usuario_id))
    except ValueError as e:
        return jsonify({"erro": str(e)}), 404


@painel_bp.route("/api/usuarios", methods=["POST"])
@exigir_permissao("pode_gerenciar_usuarios")
def api_criar_usuario():
    dados = request.json or {}
    try:
        return jsonify(criar_usuario(
            nome=dados.get("nome"), email=dados.get("email"), senha=dados.get("senha"),
            perfil_id=dados.get("perfil_id"), acesso_total=dados.get("acesso_total", False),
            unidade_ids=dados.get("unidade_ids", []), rede_ids=dados.get("rede_ids", []),
        ))
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400


@painel_bp.route("/api/usuarios/<int:usuario_id>", methods=["PUT"])
@exigir_permissao("pode_gerenciar_usuarios")
def api_editar_usuario(usuario_id):
    dados = request.json or {}
    try:
        return jsonify(editar_usuario(
            usuario_id, nome=dados.get("nome"), perfil_id=dados.get("perfil_id"),
            acesso_total=dados.get("acesso_total"), unidade_ids=dados.get("unidade_ids"),
            rede_ids=dados.get("rede_ids"), ativo=dados.get("ativo"),
            nova_senha=dados.get("nova_senha"),
        ))
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400


@painel_bp.route("/api/usuarios/<int:usuario_id>", methods=["DELETE"])
@exigir_permissao("pode_gerenciar_usuarios")
def api_excluir_usuario(usuario_id):
    try:
        excluir_usuario(usuario_id)
        return jsonify({"mensagem": "Usuario excluido."})
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400
