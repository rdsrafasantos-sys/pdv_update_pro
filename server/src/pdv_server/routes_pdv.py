"""Rotas de descoberta de lojas/PDVs, logs remotos, reenvio de venda/NFC-e,
upload e disparo de atualizacao de PDV (.zip) -- extraido de app.py
(Fase 4, divisao por dominio)."""
import json
import os
import threading
import time

from flask import Blueprint, Response, jsonify, request
from flask_login import current_user
from werkzeug.utils import secure_filename

from pdv_server import pdv_compat
from pdv_server.auth.audit import registrar_auditoria
from pdv_server.auth.routes import exigir_permissao, limiter
from pdv_server.dispatch import (
    baixar_log_pdv, extrair_payloads_de_log, get_atualizacoes_loja,
    iniciar_envio_zip, ler_conteudo_log_pdv, listar_logs_pdv,
    reenviar_nfce_service_manager, reenviar_venda_service_manager,
    reiniciar_mongo_pdv,
)
from pdv_server.discovery import encontrar_pdv, get_lojas, invalidar_cache, _tentar_requisicao
from pdv_server.rotas_comuns import com_rede, ip_cliente
from pdv_server.versioning import eh_downgrade, extrair_versao

pdv_bp = Blueprint("pdv", __name__)


@pdv_bp.route("/api/<int:rede_id>/lojas", methods=["GET"])
@com_rede
def api_lojas(contexto):
    return jsonify(get_lojas(contexto))


@pdv_bp.route("/api/<int:rede_id>/lojas/atualizar", methods=["POST"])
@com_rede
def api_lojas_atualizar(contexto):
    """Força redescoberta dos PDVs via replica set."""
    invalidar_cache(contexto.rede_id)
    return jsonify({"mensagem": "Cache invalidado", "lojas": get_lojas(contexto)})


@pdv_bp.route("/api/<int:rede_id>/ping/<loja_id>/<pdv_id>", methods=["GET"])
@com_rede
def api_ping(contexto, loja_id, pdv_id):
    pdv = encontrar_pdv(contexto, loja_id, pdv_id)
    if not pdv:
        return jsonify({"erro": "PDV não encontrado"}), 404
    try:
        r, _ = _tentar_requisicao(pdv["ip"], contexto.tailscale_site_id, "5000/ping", timeout=3)
        dados = r.json() if r.status_code == 200 else {}
        return jsonify({
            "online": r.status_code == 200,
            "ip": pdv["ip"],
            "versao_agente": dados.get("versao", "—")
        })
    except Exception:
        return jsonify({"online": False, "ip": pdv["ip"], "versao_agente": "—"})


@pdv_bp.route("/api/<int:rede_id>/ping_loja/<loja_id>", methods=["GET"])
@com_rede
def api_ping_loja(contexto, loja_id):
    loja = next((l for l in get_lojas(contexto) if l["id"] == loja_id), None)
    if not loja:
        return jsonify({"erro": "Loja não encontrada"}), 404

    resultados = {}
    threads = []

    def checar(pdv):
        try:
            r, _ = _tentar_requisicao(pdv["ip"], contexto.tailscale_site_id, "5000/ping", timeout=3)
            dados = r.json() if r.status_code == 200 else {}
            resultados[pdv["id"]] = {
                "online": r.status_code == 200,
                "versao_agente": dados.get("versao", "—")
            }
        except Exception:
            resultados[pdv["id"]] = {"online": False, "versao_agente": "—"}

    for pdv in loja["pdvs"]:
        t = threading.Thread(target=checar, args=(pdv,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=5)

    return jsonify(resultados)


@pdv_bp.route("/api/<int:rede_id>/pdv/<loja_id>/<pdv_id>/reiniciar_mongo", methods=["POST"])
@com_rede
@exigir_permissao("pode_atu_pdv_disparar")
def api_reiniciar_mongo(contexto, loja_id, pdv_id):
    pdv = encontrar_pdv(contexto, loja_id, pdv_id)
    if not pdv:
        return jsonify({"erro": "PDV não encontrado"}), 404
    resultado = reiniciar_mongo_pdv(contexto, pdv)
    return jsonify(resultado)


@pdv_bp.route("/api/<int:rede_id>/pdv/<loja_id>/<pdv_id>/logs", methods=["GET"])
@com_rede
@exigir_permissao("pode_replic_verificar")
def api_listar_logs_pdv(contexto, loja_id, pdv_id):
    pdv = encontrar_pdv(contexto, loja_id, pdv_id)
    if not pdv:
        return jsonify({"erro": "PDV não encontrado"}), 404
    resultado = listar_logs_pdv(
        contexto, pdv,
        desde=request.args.get("desde") or None,
        ate=request.args.get("ate") or None,
    )
    return jsonify(resultado)


@pdv_bp.route("/api/<int:rede_id>/pdv/<loja_id>/<pdv_id>/logs/<path:nome>", methods=["GET"])
@com_rede
@exigir_permissao("pode_replic_verificar")
def api_baixar_log_pdv(contexto, loja_id, pdv_id, nome):
    pdv = encontrar_pdv(contexto, loja_id, pdv_id)
    if not pdv:
        return jsonify({"erro": "PDV não encontrado"}), 404
    nome_seguro = os.path.basename(nome)
    r = baixar_log_pdv(contexto, pdv, nome_seguro)
    if r.status_code != 200:
        return jsonify({"erro": "Falha ao baixar o log do PDV"}), 502
    return Response(
        r.iter_content(chunk_size=8192),
        headers={"Content-Disposition": f'attachment; filename="{nome_seguro}"'},
        content_type=r.headers.get("Content-Type", "application/octet-stream"),
    )


@pdv_bp.route("/api/<int:rede_id>/pdv/<loja_id>/<pdv_id>/logs/<path:nome>/documentos", methods=["GET"])
@com_rede
@exigir_permissao("pode_replic_verificar")
def api_extrair_documentos_log(contexto, loja_id, pdv_id, nome):
    pdv = encontrar_pdv(contexto, loja_id, pdv_id)
    if not pdv:
        return jsonify({"erro": "PDV não encontrado"}), 404
    nome_seguro = os.path.basename(nome)
    texto = ler_conteudo_log_pdv(contexto, pdv, nome_seguro)
    if texto is None:
        return jsonify({"erro": "Falha ao ler o log do PDV"}), 502
    documentos = extrair_payloads_de_log(texto)
    return jsonify({"ok": True, "documentos": documentos})


@pdv_bp.route("/api/<int:rede_id>/pdv/<loja_id>/<pdv_id>/venda/reenviar", methods=["POST"])
@com_rede
@exigir_permissao("pode_reenviar_documentos")
def api_reenviar_venda(contexto, loja_id, pdv_id):
    pdv = encontrar_pdv(contexto, loja_id, pdv_id)
    if not pdv:
        return jsonify({"erro": "PDV não encontrado"}), 404
    payload = request.json or {}
    if not payload:
        return jsonify({"erro": "Payload da venda vazio"}), 400
    resultado = reenviar_venda_service_manager(contexto, payload)
    registrar_auditoria(
        current_user.email, "reenviar_venda",
        detalhes=f"rede={contexto.rede_id} pdv={pdv_id} cupom={payload.get('numeroCupom')}",
        ip=ip_cliente(),
    )
    return jsonify(resultado)


@pdv_bp.route("/api/<int:rede_id>/pdv/<loja_id>/<pdv_id>/nfce/reenviar", methods=["POST"])
@com_rede
@exigir_permissao("pode_reenviar_documentos")
def api_reenviar_nfce(contexto, loja_id, pdv_id):
    pdv = encontrar_pdv(contexto, loja_id, pdv_id)
    if not pdv:
        return jsonify({"erro": "PDV não encontrado"}), 404
    payload = request.json or {}
    if not payload:
        return jsonify({"erro": "Payload da NFC-e vazio"}), 400
    resultado = reenviar_nfce_service_manager(contexto, payload)
    registrar_auditoria(
        current_user.email, "reenviar_nfce",
        detalhes=f"rede={contexto.rede_id} pdv={pdv_id} cupom={payload.get('numeroCupom')}",
        ip=ip_cliente(),
    )
    return jsonify(resultado)


@pdv_bp.route("/api/<int:rede_id>/upload", methods=["POST"])
@com_rede
@exigir_permissao("pode_atu_pdv_upload")
@limiter.limit("10 per minute")
def api_upload(contexto):
    if "arquivo" not in request.files:
        return jsonify({"erro": "Nenhum arquivo enviado"}), 400
    arquivo = request.files["arquivo"]
    if not arquivo.filename.endswith(".zip"):
        return jsonify({"erro": "Apenas arquivos .zip são aceitos"}), 400
    nome = secure_filename(arquivo.filename)
    caminho = os.path.join(contexto.upload_dir, nome)
    arquivo.save(caminho)
    tamanho = os.path.getsize(caminho)
    registrar_auditoria(
        current_user.email, "upload_zip",
        detalhes=f"rede={contexto.rede_id} arquivo={nome} tamanho={round(tamanho / 1024 / 1024, 2)}MB",
        ip=ip_cliente(),
    )
    return jsonify({
        "mensagem": "Upload concluído",
        "arquivo": nome,
        "tamanho_mb": round(tamanho / 1024 / 1024, 2)
    })


@pdv_bp.route("/api/<int:rede_id>/arquivos", methods=["GET"])
@com_rede
def api_arquivos(contexto):
    arquivos = []
    for f in os.listdir(contexto.upload_dir):
        if f.endswith(".zip"):
            caminho = os.path.join(contexto.upload_dir, f)
            arquivos.append({
                "nome": f,
                "tamanho_mb": round(os.path.getsize(caminho) / 1024 / 1024, 2),
                "data": time.strftime("%d/%m/%Y %H:%M",
                                       time.localtime(os.path.getmtime(caminho))),
                "versao": extrair_versao(f)
            })
    arquivos.sort(key=lambda x: x["data"], reverse=True)
    return jsonify(arquivos)


@pdv_bp.route("/api/<int:rede_id>/arquivos/<nome>", methods=["DELETE"])
@com_rede
@exigir_permissao("pode_atu_pdv_limpar")
def api_deletar_arquivo(contexto, nome):
    caminho = os.path.join(contexto.upload_dir, secure_filename(nome))
    if os.path.exists(caminho):
        os.remove(caminho)
        return jsonify({"mensagem": f"{nome} removido"})
    return jsonify({"erro": "Arquivo não encontrado"}), 404


@pdv_bp.route("/api/<int:rede_id>/arquivos/limpar", methods=["DELETE"])
@com_rede
@exigir_permissao("pode_atu_pdv_limpar")
def api_limpar_arquivos(contexto):
    removidos = 0
    for f in os.listdir(contexto.upload_dir):
        if f.endswith(".zip"):
            os.remove(os.path.join(contexto.upload_dir, f))
            removidos += 1
    return jsonify({"mensagem": f"{removidos} arquivo(s) removido(s)"})


@pdv_bp.route("/api/<int:rede_id>/pdv/config-arquivo", methods=["GET"])
@com_rede
def api_config_arquivo_pdv(contexto):
    """Gera pdv_config.ini com o token DESTA rede e a auth key, para importar
    no instalador PDVAgent_Setup.exe. Escopado por rede -- nunca usar um token
    compartilhado entre clientes, quebraria o isolamento multi-tenant."""
    from pdv_server import pdv_auth_key

    conteudo = (
        "[PDVAgent]\n"
        f"TOKEN={contexto.token}\n"
        f"AUTHKEY={pdv_auth_key.ler()['key']}\n"
        "HOSTNAME=\n"
    )

    return Response(
        conteudo,
        mimetype="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="pdv_config.ini"'},
    )


@pdv_bp.route("/api/<int:rede_id>/atualizar", methods=["POST"])
@com_rede
@exigir_permissao("pode_atu_pdv_disparar")
def api_atualizar(contexto):
    dados = request.json
    loja_id = dados.get("loja_id")
    pdv_ids = dados.get("pdv_ids", [])
    arquivo = dados.get("arquivo")

    if not loja_id or not arquivo:
        return jsonify({"erro": "loja_id e arquivo são obrigatórios"}), 400

    if pdv_ids == "todos" or len(pdv_ids) != 1:
        return jsonify({"erro": "Selecione exatamente um PDV por vez para atualizar."}), 400

    caminho_zip = os.path.join(contexto.upload_dir, arquivo)
    if not os.path.exists(caminho_zip):
        return jsonify({"erro": f"Arquivo {arquivo} não encontrado"}), 404

    loja = next((l for l in get_lojas(contexto) if l["id"] == loja_id), None)
    if not loja:
        return jsonify({"erro": "Loja não encontrada"}), 404

    pdvs_alvo = [p for p in loja["pdvs"] if p["id"] in pdv_ids]
    if not pdvs_alvo:
        return jsonify({"erro": "Nenhum PDV selecionado"}), 400

    pdv = pdvs_alvo[0]
    versao_zip = extrair_versao(arquivo)
    if not versao_zip:
        return jsonify({
            "erro": "Não foi possível identificar a versão pelo nome do arquivo "
                    "(ex: VRPdvPro_7.1.0.zip). Renomeie o arquivo e envie novamente."
        }), 400

    versao_pdv = pdv.get("versao")
    if eh_downgrade(versao_zip, versao_pdv):
        return jsonify({
            "erro": f"Atualização bloqueada: o pacote é da versão {versao_zip}, mas o "
                    f"PDV {pdv['id']} já está na versão {versao_pdv}. Downgrade não é "
                    f"permitido (risco de corromper o banco)."
        }), 409

    # Verificação de compatibilidade integrador × PDVPro
    # versao_integrador vem do frontend (pre-flight check via SSH já feito lá)
    versao_int = dados.get("versao_integrador") or None
    tabela_compat = pdv_compat.buscar_tabela(contexto.integrador_dir)
    compat = pdv_compat.verificar(versao_zip, versao_int, tabela_compat)
    if compat.get("bloqueado"):
        return jsonify({"erro": compat["aviso"]}), 409

    iniciar_envio_zip(contexto, loja_id, pdv, caminho_zip)
    registrar_auditoria(
        current_user.email, "atualizar_pdv",
        detalhes=f"rede={contexto.rede_id} loja={loja_id} pdv={pdv['id']} zip={arquivo}",
        ip=ip_cliente(),
    )
    return jsonify({
        "mensagem": f"Atualização iniciada para {pdv['id']}",
        "pdvs": [pdv["id"]]
    })


@pdv_bp.route("/api/<int:rede_id>/status/<loja_id>", methods=["GET"])
@com_rede
def api_status_loja(contexto, loja_id):
    return jsonify(get_atualizacoes_loja(contexto.rede_id, loja_id))


@pdv_bp.route("/api/<int:rede_id>/status_stream/<loja_id>")
@com_rede
def api_status_stream(contexto, loja_id):
    rede_id = contexto.rede_id

    def gerar():
        ultimo = None
        while True:
            atual = json.dumps(get_atualizacoes_loja(rede_id, loja_id))
            if atual != ultimo:
                ultimo = atual
                yield f"data: {atual}\n\n"
            time.sleep(1)
    return Response(gerar(), mimetype="text/event-stream",
                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@pdv_bp.route("/api/<int:rede_id>/sysinfo_loja/<loja_id>", methods=["GET"])
@com_rede
def api_sysinfo_loja(contexto, loja_id):
    """Consulta /sysinfo do agente de todos os PDVs de uma loja em paralelo."""
    loja = next((l for l in get_lojas(contexto) if l["id"] == loja_id), None)
    if not loja:
        return jsonify({}), 404
    resultados = {}
    threads = []

    def checar(pdv):
        try:
            r, _ = _tentar_requisicao(pdv["ip"], contexto.tailscale_site_id, "5000/sysinfo", timeout=3)
            resultados[pdv["id"]] = r.json()
        except Exception:
            resultados[pdv["id"]] = {"erro": "sem resposta"}

    for pdv in loja["pdvs"]:
        t = threading.Thread(target=checar, args=(pdv,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join(timeout=5)
    return jsonify(resultados)
