"""Rotas de configuracao/atualizacao do VR Integrador via SSH e verificacao
de compatibilidade integrador x PDVPro -- extraido de app.py (Fase 4,
divisao por dominio)."""
import json as _json

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user

from pdv_server import integrador, integrador_update, pdv_compat
from pdv_server.auth.audit import registrar_auditoria
from pdv_server.auth.routes import exigir_permissao
from pdv_server.rotas_comuns import com_rede, ip_cliente, requisicao_mesma_origem

integrador_bp = Blueprint("integrador_bp", __name__)


@integrador_bp.route("/api/<int:rede_id>/integrador/config", methods=["GET"])
@com_rede
def api_integrador_config_get(contexto):
    return jsonify(integrador.carregar_config(contexto))


@integrador_bp.route("/api/<int:rede_id>/integrador/config", methods=["POST"])
@com_rede
@exigir_permissao("pode_config_integrador")
def api_integrador_config_set(contexto):
    dados = request.json or {}
    cfg = integrador.salvar_config(contexto, dados)
    registrar_auditoria(current_user.email, "config_integrador", detalhes=f"rede={contexto.rede_id}", ip=ip_cliente())
    return jsonify(cfg)


@integrador_bp.route("/api/<int:rede_id>/integrador/status", methods=["GET"])
@com_rede
def api_integrador_status(contexto):
    return jsonify(integrador.testar_status(contexto))


@integrador_bp.route("/api/<int:rede_id>/integrador/versao_atual", methods=["GET"])
@com_rede
@exigir_permissao("pode_atu_integrador")
def api_integrador_versao_atual(contexto):
    cfg = integrador.carregar_config(contexto)
    if not integrador_update.config_ssh_completa(cfg):
        return jsonify({"erro": "SSH não configurado", "versao": None})
    return jsonify(integrador_update.versao_atual(cfg))


@integrador_bp.route("/api/<int:rede_id>/integrador/atualizar_stream", methods=["GET"])
@com_rede
@exigir_permissao("pode_atu_integrador")
def api_integrador_atualizar_stream(contexto):
    if not requisicao_mesma_origem():
        return jsonify({"erro": "Requisição bloqueada (verificação de origem)"}), 403
    nova_versao = request.args.get("versao", "").strip()
    if not nova_versao:
        return jsonify({"erro": "Parâmetro 'versao' obrigatório"}), 400
    cfg = integrador.carregar_config(contexto)
    if not integrador_update.config_ssh_completa(cfg):
        return jsonify({"erro": "SSH não configurado"}), 400

    def gerar():
        for evento in integrador_update.atualizar_stream(cfg, nova_versao):
            yield f"data: {_json.dumps(evento, ensure_ascii=False)}\n\n"

    return current_app.response_class(gerar(), mimetype="text/event-stream",
                                       headers={"Cache-Control": "no-cache",
                                                "X-Accel-Buffering": "no"})


@integrador_bp.route("/api/<int:rede_id>/compat/verificar", methods=["GET"])
@com_rede
@exigir_permissao("pode_atu_pdv_disparar")
def api_compat_verificar(contexto):
    versao_pdvpro = request.args.get("versao_pdvpro", "").strip()
    if not versao_pdvpro:
        return jsonify({"erro": "versao_pdvpro obrigatório"}), 400
    tabela = pdv_compat.buscar_tabela(contexto.integrador_dir)
    cfg_int = integrador.carregar_config(contexto)
    versao_int = None
    if integrador_update.config_ssh_completa(cfg_int):
        res = integrador_update.versao_atual(cfg_int)
        versao_int = res.get("versao")
    return jsonify(pdv_compat.verificar(versao_pdvpro, versao_int, tabela))


@integrador_bp.route("/api/<int:rede_id>/compat/tabela", methods=["GET"])
@com_rede
def api_compat_tabela(contexto):
    forcar = request.args.get("forcar", "false").lower() == "true"
    tabela = pdv_compat.buscar_tabela(contexto.integrador_dir, forcar=forcar)
    return jsonify({"tabela": tabela})


@integrador_bp.route("/api/<int:rede_id>/integrador/container_status", methods=["GET"])
@com_rede
@exigir_permissao("pode_atu_integrador")
def api_integrador_container_status(contexto):
    cfg = integrador.carregar_config(contexto)
    if not integrador_update.config_ssh_completa(cfg):
        return jsonify({"ok": False, "rodando": False, "status": "ssh_nao_configurado",
                        "erro": "SSH não configurado"})
    return jsonify(integrador_update.container_status(cfg))


@integrador_bp.route("/api/<int:rede_id>/integrador/logs", methods=["GET"])
@com_rede
@exigir_permissao("pode_atu_integrador")
def api_integrador_logs(contexto):
    cfg = integrador.carregar_config(contexto)
    if not integrador_update.config_ssh_completa(cfg):
        return jsonify({"ok": False, "linhas": [], "erro": "SSH não configurado"})
    linhas = min(int(request.args.get("linhas", 100)), 500)
    return jsonify(integrador_update.logs_container(cfg, linhas))


@integrador_bp.route("/api/<int:rede_id>/integrador/container_acao", methods=["POST"])
@com_rede
@exigir_permissao("pode_atu_integrador")
def api_integrador_container_acao(contexto):
    acao = (request.json or {}).get("acao", "")
    cfg = integrador.carregar_config(contexto)
    if not integrador_update.config_ssh_completa(cfg):
        return jsonify({"ok": False, "erro": "SSH não configurado"}), 400
    resultado = integrador_update.start_stop_container(cfg, acao)
    registrar_auditoria(current_user.email, f"integrador_{acao}",
                        detalhes=f"rede={contexto.rede_id}", ip=ip_cliente())
    return jsonify(resultado)
