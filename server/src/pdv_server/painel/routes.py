from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from pdv_server.auth.audit import registrar_auditoria
from pdv_server.auth.gestao import (
    alternar_ativa_rede, criar_perfil, criar_rede, criar_rede_da_instalacao,
    criar_unidade, criar_usuario, editar_perfil, editar_rede, editar_unidade,
    editar_usuario, excluir_perfil, excluir_unidade, excluir_usuario,
    gerar_proximo_site_id, gerar_script_instalacao, listar_perfis,
    listar_redes, listar_site_ids_instalacao, listar_unidades,
    listar_usuarios, obter_instalacao, obter_rede, obter_usuario,
    processar_callback_instalacao, redes_visiveis_para, status_pool,
    usuario_pode_acessar_rede,
)
from pdv_server.auth.routes import exigir_permissao, exigir_super_admin, limiter

painel_bp = Blueprint("painel", __name__)


def _ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr or "")


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


@painel_bp.route("/instalacao")
@exigir_permissao("pode_gerenciar_redes")
def instalacao_pagina():
    return render_template("instalacao.html")


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
        resultado = criar_unidade(dados.get("nome"))
        registrar_auditoria(current_user.email, "criar_unidade", detalhes=dados.get("nome", ""), ip=_ip())
        return jsonify(resultado)
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400


@painel_bp.route("/api/unidades/<int:unidade_id>", methods=["PUT"])
@exigir_super_admin
def api_editar_unidade(unidade_id):
    dados = request.json or {}
    try:
        resultado = editar_unidade(unidade_id, dados.get("nome"))
        registrar_auditoria(current_user.email, "editar_unidade", detalhes=f"id={unidade_id}", ip=_ip())
        return jsonify(resultado)
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400


@painel_bp.route("/api/unidades/<int:unidade_id>", methods=["DELETE"])
@exigir_super_admin
def api_excluir_unidade(unidade_id):
    try:
        excluir_unidade(unidade_id)
        registrar_auditoria(current_user.email, "excluir_unidade", detalhes=f"id={unidade_id}", ip=_ip())
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
        resultado = criar_rede(
            nome_fantasia=dados.get("nome_fantasia"),
            razao_social=dados.get("razao_social", ""),
            unidade_id=unidade_id,
            mongo_uri=dados.get("mongo_uri"),
            token=dados.get("token"),
            tailscale_site_id=dados.get("tailscale_site_id", ""),
            cnpj=dados.get("cnpj", ""),
        )
        registrar_auditoria(current_user.email, "criar_rede", detalhes=dados.get("nome_fantasia", ""), ip=_ip())
        return jsonify(resultado)
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400


@painel_bp.route("/api/redes/<int:rede_id>", methods=["PUT"])
@exigir_permissao("pode_gerenciar_redes")
def api_editar_rede(rede_id):
    if not usuario_pode_acessar_rede(int(current_user.id), rede_id):
        return jsonify({"erro": "Sem acesso a esta rede"}), 403
    dados = request.json or {}
    try:
        resultado = editar_rede(
            rede_id,
            nome_fantasia=dados.get("nome_fantasia"),
            razao_social=dados.get("razao_social"),
            unidade_id=dados.get("unidade_id"),
            mongo_uri=dados.get("mongo_uri"),
            token=dados.get("token"),
            tailscale_site_id=dados.get("tailscale_site_id"),
            cnpj=dados.get("cnpj"),
        )
        registrar_auditoria(current_user.email, "editar_rede", detalhes=f"id={rede_id}", ip=_ip())
        return jsonify(resultado)
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400


@painel_bp.route("/api/redes/<int:rede_id>/ativa", methods=["POST"])
@exigir_permissao("pode_gerenciar_redes")
def api_alternar_rede(rede_id):
    if not usuario_pode_acessar_rede(int(current_user.id), rede_id):
        return jsonify({"erro": "Sem acesso a esta rede"}), 403
    dados = request.json or {}
    ativa = dados.get("ativa", True)
    try:
        resultado = alternar_ativa_rede(rede_id, ativa)
        registrar_auditoria(current_user.email, "alternar_rede", detalhes=f"id={rede_id} ativa={ativa}", ip=_ip())
        return jsonify(resultado)
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400


# ── API: Instalacao (alocacao de Tailscale Site ID) ─────────────

@painel_bp.route("/api/instalacao/site-ids", methods=["GET"])
@exigir_permissao("pode_gerenciar_redes")
def api_listar_site_ids():
    return jsonify(listar_site_ids_instalacao())


@painel_bp.route("/api/instalacao/site-ids", methods=["POST"])
@exigir_permissao("pode_gerenciar_redes")
def api_gerar_site_id():
    dados = request.json or {}
    try:
        registro = gerar_proximo_site_id(
            cliente_cnpj=dados.get("cliente_cnpj", ""),
            cliente_nome=dados.get("cliente_nome", ""),
            usuario_email=current_user.email,
        )
        return jsonify(registro)
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400


@painel_bp.route("/api/instalacao/<int:instalacao_id>", methods=["GET"])
@exigir_permissao("pode_gerenciar_redes")
def api_obter_instalacao(instalacao_id):
    try:
        return jsonify(obter_instalacao(instalacao_id))
    except ValueError as e:
        return jsonify({"erro": str(e)}), 404


@painel_bp.route("/api/instalacao/<int:instalacao_id>/script", methods=["POST"])
@exigir_permissao("pode_gerenciar_redes")
def api_gerar_script_instalacao(instalacao_id):
    dados = request.json or {}
    try:
        script = gerar_script_instalacao(instalacao_id, erp_ip=dados.get("erp_ip", ""))
        return jsonify({"script": script, "instalacao": obter_instalacao(instalacao_id)})
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400


# Callback do script rodando no service manager do cliente -- sem login,
# autenticado pelo token de uso unico gerado junto com o script (ver
# gerar_script_instalacao). Rate limit por seguranca extra, ja que e o
# unico endpoint deste blueprint sem sessao por tras.
@painel_bp.route("/api/instalacao/<int:instalacao_id>/criar-rede", methods=["POST"])
@exigir_permissao("pode_gerenciar_redes")
def api_criar_rede_da_instalacao(instalacao_id):
    dados = request.json or {}
    unidade_id = dados.get("unidade_id")
    if not current_user.acesso_total and unidade_id not in current_user.unidade_ids:
        return jsonify({"erro": "Sem acesso a essa unidade"}), 403
    try:
        return jsonify(criar_rede_da_instalacao(
            instalacao_id,
            token=dados.get("token", ""),
            unidade_id=unidade_id,
            mongo_uri=dados.get("mongo_uri", ""),
        ))
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400


@painel_bp.route("/api/instalacao/pool/status", methods=["GET"])
@exigir_permissao("pode_gerenciar_redes")
def api_status_pool():
    return jsonify(status_pool())


@painel_bp.route("/api/instalacao/callback/<token_callback>", methods=["POST"])
@limiter.limit("30 per minute")
def api_callback_instalacao(token_callback):
    dados = request.json or {}
    try:
        return jsonify(processar_callback_instalacao(token_callback, dados))
    except ValueError as e:
        return jsonify({"erro": str(e)}), 404


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
        resultado = criar_perfil(
            nome=dados.get("nome"), descricao=dados.get("descricao", ""),
            pode_gerenciar_redes=dados.get("pode_gerenciar_redes", False),
            pode_gerenciar_usuarios=dados.get("pode_gerenciar_usuarios", False),
            somente_leitura=dados.get("somente_leitura", False),
            pode_ver_fiscal=dados.get("pode_ver_fiscal", False),
            pode_atu_agente=dados.get("pode_atu_agente", False),
            pode_atu_pdv_upload=dados.get("pode_atu_pdv_upload", False),
            pode_atu_pdv_disparar=dados.get("pode_atu_pdv_disparar", False),
            pode_atu_pdv_limpar=dados.get("pode_atu_pdv_limpar", False),
            pode_atu_integrador=dados.get("pode_atu_integrador", False),
            pode_replic_verificar=dados.get("pode_replic_verificar", False),
            pode_replic_config=dados.get("pode_replic_config", False),
            pode_config_banco=dados.get("pode_config_banco", False),
            pode_config_integrador=dados.get("pode_config_integrador", False),
            pode_reenviar_documentos=dados.get("pode_reenviar_documentos", False),
        )
        registrar_auditoria(current_user.email, "criar_perfil", detalhes=dados.get("nome", ""), ip=_ip())
        return jsonify(resultado)
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400


@painel_bp.route("/api/perfis/<int:perfil_id>", methods=["PUT"])
@exigir_permissao("pode_gerenciar_usuarios")
def api_editar_perfil(perfil_id):
    dados = request.json or {}
    try:
        resultado = editar_perfil(
            perfil_id, nome=dados.get("nome"), descricao=dados.get("descricao"),
            pode_gerenciar_redes=dados.get("pode_gerenciar_redes"),
            pode_gerenciar_usuarios=dados.get("pode_gerenciar_usuarios"),
            somente_leitura=dados.get("somente_leitura"),
            pode_ver_fiscal=dados.get("pode_ver_fiscal"),
            pode_atu_agente=dados.get("pode_atu_agente"),
            pode_atu_pdv_upload=dados.get("pode_atu_pdv_upload"),
            pode_atu_pdv_disparar=dados.get("pode_atu_pdv_disparar"),
            pode_atu_pdv_limpar=dados.get("pode_atu_pdv_limpar"),
            pode_atu_integrador=dados.get("pode_atu_integrador"),
            pode_replic_verificar=dados.get("pode_replic_verificar"),
            pode_replic_config=dados.get("pode_replic_config"),
            pode_config_banco=dados.get("pode_config_banco"),
            pode_config_integrador=dados.get("pode_config_integrador"),
            pode_reenviar_documentos=dados.get("pode_reenviar_documentos"),
        )
        registrar_auditoria(current_user.email, "editar_perfil", detalhes=f"id={perfil_id}", ip=_ip())
        return jsonify(resultado)
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400


@painel_bp.route("/api/perfis/<int:perfil_id>", methods=["DELETE"])
@exigir_permissao("pode_gerenciar_usuarios")
def api_excluir_perfil(perfil_id):
    try:
        excluir_perfil(perfil_id)
        registrar_auditoria(current_user.email, "excluir_perfil", detalhes=f"id={perfil_id}", ip=_ip())
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
        resultado = criar_usuario(
            nome=dados.get("nome"), email=dados.get("email"), senha=dados.get("senha"),
            perfil_id=dados.get("perfil_id"), acesso_total=dados.get("acesso_total", False),
            unidade_ids=dados.get("unidade_ids", []), rede_ids=dados.get("rede_ids", []),
        )
        registrar_auditoria(current_user.email, "criar_usuario", detalhes=dados.get("email", ""), ip=_ip())
        return jsonify(resultado)
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400


@painel_bp.route("/api/usuarios/<int:usuario_id>", methods=["PUT"])
@exigir_permissao("pode_gerenciar_usuarios")
def api_editar_usuario(usuario_id):
    dados = request.json or {}
    try:
        resultado = editar_usuario(
            usuario_id, nome=dados.get("nome"), perfil_id=dados.get("perfil_id"),
            acesso_total=dados.get("acesso_total"), unidade_ids=dados.get("unidade_ids"),
            rede_ids=dados.get("rede_ids"), ativo=dados.get("ativo"),
            nova_senha=dados.get("nova_senha"),
        )
        detalhes = f"id={usuario_id}"
        if dados.get("nova_senha"):
            detalhes += " senha_alterada=true"
        registrar_auditoria(current_user.email, "editar_usuario", detalhes=detalhes, ip=_ip())
        return jsonify(resultado)
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400


@painel_bp.route("/api/usuarios/<int:usuario_id>", methods=["DELETE"])
@exigir_permissao("pode_gerenciar_usuarios")
def api_excluir_usuario(usuario_id):
    try:
        excluir_usuario(usuario_id)
        registrar_auditoria(current_user.email, "excluir_usuario", detalhes=f"id={usuario_id}", ip=_ip())
        return jsonify({"mensagem": "Usuario excluido."})
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400
