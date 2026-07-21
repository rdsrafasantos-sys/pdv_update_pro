"""Rotas de atualizacao do agente PDV (agente.exe/status_pdv.exe enviados
aos terminais) -- extraido de app.py (Fase 4, divisao por dominio)."""
import glob
import os
import time

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from pdv_server.auth.audit import registrar_auditoria
from pdv_server.auth.routes import exigir_permissao, limiter
from pdv_server.dispatch import enviar_agente_para_pdvs
from pdv_server.discovery import get_lojas
from pdv_server.rotas_comuns import com_rede, ip_cliente

agente_bp = Blueprint("agente", __name__)


@agente_bp.route("/api/agente/info", methods=["GET"])
@login_required
def api_agente_info():
    """Retorna metadados dos instaladores disponíveis para download (agente + status_pdv)."""
    def _info(nome):
        candidatos = glob.glob(f"/opt/pdv-server/uploads/*/{nome}")
        if not candidatos:
            return {"disponivel": False}
        caminho = max(candidatos, key=os.path.getmtime)
        return {
            "disponivel": True,
            "tamanho_mb": round(os.path.getsize(caminho) / 1024 / 1024, 2),
            "data": time.strftime("%d/%m/%Y %H:%M", time.localtime(os.path.getmtime(caminho))),
        }

    return jsonify({
        "agente": _info("agente.exe"),
        "status_pdv": _info("status_pdv.exe"),
    })


@agente_bp.route("/api/<int:rede_id>/upload_agente", methods=["POST"])
@com_rede
@exigir_permissao("pode_atu_agente")
@limiter.limit("10 per minute")
def api_upload_agente(contexto):
    """Recebe agente.exe ou status_pdv.exe e salva no servidor."""
    if "arquivo" not in request.files:
        return jsonify({"erro": "Nenhum arquivo enviado"}), 400
    arquivo = request.files["arquivo"]
    nome = arquivo.filename.lower()
    if nome not in ("agente.exe", "status_pdv.exe"):
        return jsonify({"erro": "Apenas agente.exe ou status_pdv.exe sao aceitos"}), 400
    caminho = os.path.join(contexto.upload_dir, nome)
    arquivo.save(caminho)
    tamanho = os.path.getsize(caminho)
    registrar_auditoria(
        current_user.email, "upload_agente",
        detalhes=f"rede={contexto.rede_id} arquivo={nome} tamanho={round(tamanho / 1024 / 1024, 2)}MB",
        ip=ip_cliente(),
    )
    return jsonify({
        "mensagem": f"Upload de {nome} concluido",
        "tamanho_mb": round(tamanho / 1024 / 1024, 2)
    })


@agente_bp.route("/api/<int:rede_id>/versao_agente", methods=["GET"])
@com_rede
def api_versao_agente(contexto):
    """Verifica se existe agente.exe disponivel para distribuicao."""
    caminho = os.path.join(contexto.upload_dir, "agente.exe")
    if os.path.exists(caminho):
        return jsonify({
            "disponivel": True,
            "tamanho_mb": round(os.path.getsize(caminho) / 1024 / 1024, 2),
            "data": time.strftime("%d/%m/%Y %H:%M",
                                   time.localtime(os.path.getmtime(caminho)))
        })
    return jsonify({"disponivel": False})


@agente_bp.route("/api/<int:rede_id>/atualizar_agente", methods=["POST"])
@com_rede
@exigir_permissao("pode_atu_agente")
def api_atualizar_agente(contexto):
    """Envia novo agente.exe para PDVs selecionados."""
    dados = request.json
    loja_id = dados.get("loja_id")
    pdv_ids = dados.get("pdv_ids", [])

    caminho_exe = os.path.join(contexto.upload_dir, "agente.exe")
    if not os.path.exists(caminho_exe):
        return jsonify({"erro": "Nenhum agente.exe disponivel. Faca upload primeiro."}), 404

    loja = next((l for l in get_lojas(contexto) if l["id"] == loja_id), None)
    if not loja:
        return jsonify({"erro": "Loja nao encontrada"}), 404

    pdvs_alvo = loja["pdvs"] if pdv_ids == "todos" else \
        [p for p in loja["pdvs"] if p["id"] in pdv_ids]

    if not pdvs_alvo:
        return jsonify({"erro": "Nenhum PDV selecionado"}), 400

    caminho_status = os.path.join(contexto.upload_dir, "status_pdv.exe")
    resultados = enviar_agente_para_pdvs(
        contexto, caminho_exe, pdvs_alvo,
        caminho_status=caminho_status if os.path.exists(caminho_status) else None
    )
    registrar_auditoria(
        current_user.email, "atualizar_agente",
        detalhes=f"rede={contexto.rede_id} loja={loja_id} pdvs={len(pdvs_alvo)}",
        ip=ip_cliente(),
    )
    return jsonify({"resultados": resultados})
