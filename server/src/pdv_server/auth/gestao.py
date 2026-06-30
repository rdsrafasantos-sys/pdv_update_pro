"""CRUD de Unidades, Redes, Usuarios e Perfis (Fases 1 e 2). Segredos de
cada rede (Mongo URI, token, Tailscale Site ID) sao sempre cifrados antes
de ir para o banco -- ver auth/crypto.py. Quem usa: rotas em
painel/routes.py e app.py (resolucao de permissao)."""
import re
import secrets

from pdv_server import tailscale_api
from pdv_server.auth.crypto import cifrar, decifrar
from pdv_server.auth.models import (
    InstalacaoSiteId, Perfil, Rede, SessionLocal, Unidade, Usuario,
)
from pdv_server.auth.security import gerar_hash_senha
from pdv_server.config import PAINEL_CALLBACK_URL

_PESOS_CNPJ_1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
_PESOS_CNPJ_2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]


def _digito_verificador_cnpj(numeros, pesos):
    soma = sum(n * p for n, p in zip(numeros, pesos))
    resto = soma % 11
    return 0 if resto < 2 else 11 - resto


def validar_cnpj(cnpj):
    """Normaliza (so digitos) e valida um CNPJ pelo algoritmo oficial dos
    digitos verificadores. Levanta ValueError com mensagem amigavel se
    invalido; retorna a string de 14 digitos (sem pontuacao) se valido."""
    digitos = re.sub(r"\D", "", cnpj or "")
    if len(digitos) != 14:
        raise ValueError("CNPJ precisa ter 14 digitos.")
    if digitos == digitos[0] * 14:
        raise ValueError("CNPJ invalido.")
    numeros = [int(d) for d in digitos]
    d1 = _digito_verificador_cnpj(numeros[:12], _PESOS_CNPJ_1)
    d2 = _digito_verificador_cnpj(numeros[:12] + [d1], _PESOS_CNPJ_2)
    if numeros[12] != d1 or numeros[13] != d2:
        raise ValueError("CNPJ invalido (digito verificador nao confere).")
    return digitos


# ── Unidades ─────────────────────────────────────────────────

def listar_unidades():
    db = SessionLocal()
    try:
        unidades = db.query(Unidade).order_by(Unidade.nome).all()
        return [
            {"id": u.id, "nome": u.nome, "total_redes": len(u.redes)}
            for u in unidades
        ]
    finally:
        db.close()


def criar_unidade(nome):
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("Nome da unidade e obrigatorio.")
    db = SessionLocal()
    try:
        if db.query(Unidade).filter_by(nome=nome).first():
            raise ValueError(f"Ja existe uma unidade chamada '{nome}'.")
        unidade = Unidade(nome=nome)
        db.add(unidade)
        db.commit()
        return {"id": unidade.id, "nome": unidade.nome}
    finally:
        db.close()


def editar_unidade(unidade_id, nome):
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("Nome da unidade e obrigatorio.")
    db = SessionLocal()
    try:
        unidade = db.get(Unidade, unidade_id)
        if not unidade:
            raise ValueError("Unidade nao encontrada.")
        unidade.nome = nome
        db.commit()
        return {"id": unidade.id, "nome": unidade.nome}
    finally:
        db.close()


def excluir_unidade(unidade_id):
    db = SessionLocal()
    try:
        unidade = db.get(Unidade, unidade_id)
        if not unidade:
            raise ValueError("Unidade nao encontrada.")
        if unidade.redes:
            raise ValueError(
                "Esta unidade tem redes cadastradas -- mova ou remova as "
                "redes antes de excluir a unidade."
            )
        db.delete(unidade)
        db.commit()
    finally:
        db.close()


# ── Redes ────────────────────────────────────────────────────

def _rede_para_dict(rede, com_segredos=False):
    dados = {
        "id": rede.id,
        "nome": rede.nome,
        "cnpj": rede.cnpj,
        "unidade_id": rede.unidade_id,
        "unidade_nome": rede.unidade.nome if rede.unidade else None,
        "ativa": rede.ativa,
        "tem_tailscale_site_id": bool(rede.tailscale_site_id_cifrado),
        "criado_em": rede.criado_em.strftime("%Y-%m-%d %H:%M") if rede.criado_em else None,
    }
    if com_segredos:
        dados["mongo_uri"] = decifrar(rede.mongo_uri_cifrado)
        dados["token"] = decifrar(rede.token_cifrado)
        dados["tailscale_site_id"] = decifrar(rede.tailscale_site_id_cifrado) or ""
    return dados


def listar_redes(unidade_id=None):
    db = SessionLocal()
    try:
        query = db.query(Rede)
        if unidade_id:
            query = query.filter_by(unidade_id=unidade_id)
        redes = query.order_by(Rede.nome).all()
        return [_rede_para_dict(r) for r in redes]
    finally:
        db.close()


def obter_rede(rede_id, com_segredos=False):
    db = SessionLocal()
    try:
        rede = db.get(Rede, rede_id)
        if not rede:
            raise ValueError("Rede nao encontrada.")
        return _rede_para_dict(rede, com_segredos=com_segredos)
    finally:
        db.close()


def _checar_cnpj_disponivel(db, cnpj, ignorar_rede_id=None):
    """CNPJ identifica a empresa de forma unica -- nome pode se repetir
    entre clientes diferentes, CNPJ nao pode."""
    query = db.query(Rede).filter_by(cnpj=cnpj)
    if ignorar_rede_id:
        query = query.filter(Rede.id != ignorar_rede_id)
    existente = query.first()
    if existente:
        raise ValueError(f"Ja existe uma rede com este CNPJ: '{existente.nome}'.")


def criar_rede(nome, unidade_id, mongo_uri, token, tailscale_site_id="", cnpj=""):
    nome = (nome or "").strip()
    mongo_uri = (mongo_uri or "").strip()
    token = (token or "").strip()
    if not nome or not unidade_id or not mongo_uri or not token:
        raise ValueError("Nome, unidade, Mongo URI e token sao obrigatorios.")
    cnpj_normalizado = validar_cnpj(cnpj) if cnpj else None

    db = SessionLocal()
    try:
        if not db.get(Unidade, unidade_id):
            raise ValueError("Unidade nao encontrada.")
        if db.query(Rede).filter_by(nome=nome).first():
            raise ValueError(f"Ja existe uma rede chamada '{nome}'.")
        if cnpj_normalizado:
            _checar_cnpj_disponivel(db, cnpj_normalizado)

        rede = Rede(
            nome=nome, unidade_id=unidade_id, cnpj=cnpj_normalizado,
            mongo_uri_cifrado=cifrar(mongo_uri),
            token_cifrado=cifrar(token),
            tailscale_site_id_cifrado=cifrar(tailscale_site_id) if tailscale_site_id else None,
            ativa=True,
        )
        db.add(rede)
        db.commit()
        return _rede_para_dict(rede)
    finally:
        db.close()


def editar_rede(rede_id, nome, unidade_id, mongo_uri, token, tailscale_site_id="", cnpj=None):
    db = SessionLocal()
    try:
        rede = db.get(Rede, rede_id)
        if not rede:
            raise ValueError("Rede nao encontrada.")

        if nome:
            rede.nome = nome.strip()
        if unidade_id:
            if not db.get(Unidade, unidade_id):
                raise ValueError("Unidade nao encontrada.")
            rede.unidade_id = unidade_id
        if mongo_uri:
            rede.mongo_uri_cifrado = cifrar(mongo_uri.strip())
        if token:
            rede.token_cifrado = cifrar(token.strip())
        if tailscale_site_id is not None:
            rede.tailscale_site_id_cifrado = cifrar(tailscale_site_id) if tailscale_site_id else None
        if cnpj is not None:
            cnpj_normalizado = validar_cnpj(cnpj) if cnpj else None
            if cnpj_normalizado:
                _checar_cnpj_disponivel(db, cnpj_normalizado, ignorar_rede_id=rede_id)
            rede.cnpj = cnpj_normalizado

        db.commit()
        return _rede_para_dict(rede)
    finally:
        db.close()


def alternar_ativa_rede(rede_id, ativa):
    db = SessionLocal()
    try:
        rede = db.get(Rede, rede_id)
        if not rede:
            raise ValueError("Rede nao encontrada.")
        rede.ativa = bool(ativa)
        db.commit()
        return _rede_para_dict(rede)
    finally:
        db.close()


# ── Instalacao (alocacao de Tailscale Site ID) ──────────────────
# Site ID precisa ser unico pra sempre na tailnet -- mesmo que a rede
# correspondente nunca seja criada, ou seja excluida depois, o numero
# alocado aqui nunca pode ser reaproveitado. Por isso o "maior valor" e
# calculado tanto a partir das Redes existentes (campo pode ter sido
# preenchido a mao, como o Site ID 7 da TEST) quanto do historico desta
# tabela, e o registro e gravado ANTES de a rede existir.

def _maior_site_id_existente(db):
    maior_redes = 0
    for rede in db.query(Rede).all():
        if rede.tailscale_site_id_cifrado:
            valor = decifrar(rede.tailscale_site_id_cifrado)
            if valor and valor.strip().isdigit():
                maior_redes = max(maior_redes, int(valor))
    maior_alocados = max(
        (r.site_id for r in db.query(InstalacaoSiteId).all()), default=0
    )
    return max(maior_redes, maior_alocados)


def listar_site_ids_instalacao():
    db = SessionLocal()
    try:
        registros = db.query(InstalacaoSiteId).order_by(InstalacaoSiteId.site_id.desc()).all()
        return [_instalacao_para_dict(r) for r in registros]
    finally:
        db.close()


def gerar_proximo_site_id(cliente_cnpj, cliente_nome="", usuario_email=""):
    cnpj_normalizado = validar_cnpj(cliente_cnpj)
    db = SessionLocal()
    try:
        _checar_cnpj_disponivel(db, cnpj_normalizado)
        if db.query(InstalacaoSiteId).filter_by(cliente_cnpj=cnpj_normalizado).first():
            raise ValueError("Ja existe um Site ID gerado para este CNPJ -- veja o historico abaixo.")

        proximo = _maior_site_id_existente(db) + 1
        registro = InstalacaoSiteId(
            site_id=proximo,
            cliente_nome=(cliente_nome or "").strip() or None,
            cliente_cnpj=cnpj_normalizado,
            usuario_email=(usuario_email or "").strip() or None,
        )
        db.add(registro)
        db.commit()
        return {
            "id": registro.id,
            "site_id": registro.site_id,
            "cliente_nome": registro.cliente_nome,
            "cliente_cnpj": registro.cliente_cnpj,
            "usuario_email": registro.usuario_email,
            "criado_em": registro.criado_em.strftime("%Y-%m-%d %H:%M"),
        }
    finally:
        db.close()


def _instalacao_para_dict(registro):
    return {
        "id": registro.id,
        "site_id": registro.site_id,
        "cliente_nome": registro.cliente_nome,
        "cliente_cnpj": registro.cliente_cnpj,
        "usuario_email": registro.usuario_email,
        "status": registro.status,
        "erp_ip": registro.erp_ip,
        "tailscale_hostname": registro.tailscale_hostname,
        "tailscale_ip": registro.tailscale_ip,
        "faixas_detectadas": registro.faixas_detectadas,
        "prefixos_ipv6": registro.prefixos_ipv6,
        "erro_mensagem": registro.erro_mensagem,
        "criado_em": registro.criado_em.strftime("%Y-%m-%d %H:%M") if registro.criado_em else None,
        "atualizado_em": registro.atualizado_em.strftime("%Y-%m-%d %H:%M") if registro.atualizado_em else None,
    }


def obter_instalacao(instalacao_id):
    db = SessionLocal()
    try:
        registro = db.get(InstalacaoSiteId, instalacao_id)
        if not registro:
            raise ValueError("Instalacao nao encontrada.")
        return _instalacao_para_dict(registro)
    finally:
        db.close()


def gerar_script_instalacao(instalacao_id, erp_ip=""):
    """Gera (ou regenera) o script do service manager para esta instalacao.
    Exige a credencial da API do Tailscale configurada -- sem ela nao tem
    como mintar uma auth key de uso unico para o script."""
    from pdv_server.instalacao_script import gerar_script

    if not tailscale_api.automacao_disponivel():
        raise ValueError(
            "PDV_TAILSCALE_OAUTH_CLIENT_ID/SECRET nao configurados neste "
            "servidor -- nao e possivel gerar a auth key do script."
        )
    if not PAINEL_CALLBACK_URL:
        raise ValueError(
            "PDV_PAINEL_CALLBACK_URL nao configurada -- o script nao "
            "saberia para onde reportar o progresso."
        )

    db = SessionLocal()
    try:
        registro = db.get(InstalacaoSiteId, instalacao_id)
        if not registro:
            raise ValueError("Instalacao nao encontrada.")

        registro.erp_ip = (erp_ip or "").strip() or None
        if not registro.token_callback:
            registro.token_callback = secrets.token_urlsafe(32)

        auth_key = tailscale_api.criar_auth_key(
            tags=["tag:pdv-service-manager"],
            descricao=f"instalacao-site-id-{registro.site_id}",
        )

        callback_url = f"{PAINEL_CALLBACK_URL.rstrip('/')}/api/instalacao/callback/{registro.token_callback}"
        script = gerar_script(registro.site_id, auth_key, registro.erp_ip, callback_url)

        registro.status = "script_gerado"
        registro.erro_mensagem = None
        db.commit()
        return script
    finally:
        db.close()


def processar_callback_instalacao(token_callback, payload):
    """Recebe o status reportado pelo script rodando no service manager.
    Quando o status e 'concluido', tenta automatizar o resto (aprovar
    rotas + acrescentar os prefixos no ACL) -- se a automacao falhar (ou
    nao estiver configurada), a instalacao fica marcada com o erro pra
    resolver manualmente, mas o que o script ja fez (Tailscale conectado,
    rotas anunciadas) nao se perde."""
    db = SessionLocal()
    try:
        registro = db.query(InstalacaoSiteId).filter_by(token_callback=token_callback).first()
        if not registro:
            raise ValueError("Token de callback invalido.")

        status = payload.get("status", "")
        if status == "erro":
            registro.status = "erro"
            registro.erro_mensagem = payload.get("mensagem", "Erro desconhecido no script.")
        elif status == "conectado":
            registro.status = "conectado"
            registro.tailscale_ip = payload.get("tailscale_ip")
            registro.tailscale_hostname = payload.get("tailscale_hostname")
        elif status == "concluido":
            registro.tailscale_ip = payload.get("tailscale_ip") or registro.tailscale_ip
            registro.tailscale_hostname = payload.get("tailscale_hostname") or registro.tailscale_hostname
            registro.faixas_detectadas = payload.get("faixas")
            registro.prefixos_ipv6 = payload.get("prefixos")
            db.commit()
            _finalizar_automacao_instalacao(registro)
            db.commit()
            return _instalacao_para_dict(registro)
        else:
            registro.status = "iniciando"

        db.commit()
        return _instalacao_para_dict(registro)
    finally:
        db.close()


def _finalizar_automacao_instalacao(registro):
    """Best-effort: aprova as rotas anunciadas e acrescenta os prefixos no
    ACL via API do Tailscale. So roda se a credencial estiver configurada
    -- senao, deixa status 'concluido_manual' com os prefixos visiveis pra
    quem administra colar manualmente no admin console."""
    prefixos = [p for p in (registro.prefixos_ipv6 or "").split(",") if p]
    if not prefixos:
        registro.status = "erro"
        registro.erro_mensagem = "Script concluiu mas nao reportou nenhum prefixo IPv6."
        return

    if not tailscale_api.automacao_disponivel():
        registro.status = "concluido_manual"
        return

    try:
        dispositivo = tailscale_api.obter_dispositivo_por_hostname(registro.tailscale_hostname)
        if not dispositivo:
            raise ValueError(f"Dispositivo '{registro.tailscale_hostname}' nao encontrado na tailnet ainda.")
        tailscale_api.aprovar_rotas(dispositivo["id"], prefixos)
        tailscale_api.adicionar_prefixos_ao_grant("tag:pdv-service-manager", prefixos)
        registro.status = "concluido"
        registro.erro_mensagem = None
    except Exception as e:
        registro.status = "concluido_pendente_acl"
        registro.erro_mensagem = (
            f"Service manager conectado e rotas anunciadas, mas a automacao do "
            f"ACL falhou: {e}. Aprove a rota e acrescente os prefixos manualmente."
        )


# ── Perfis ───────────────────────────────────────────────────

def _perfil_para_dict(perfil):
    return {
        "id": perfil.id,
        "nome": perfil.nome,
        "descricao": perfil.descricao,
        "pode_gerenciar_redes": bool(perfil.pode_gerenciar_redes),
        "pode_gerenciar_usuarios": bool(perfil.pode_gerenciar_usuarios),
        "somente_leitura": bool(perfil.somente_leitura),
        "total_usuarios": len(perfil.usuarios),
    }


def listar_perfis():
    db = SessionLocal()
    try:
        perfis = db.query(Perfil).order_by(Perfil.nome).all()
        return [_perfil_para_dict(p) for p in perfis]
    finally:
        db.close()


def criar_perfil(nome, descricao="", pode_gerenciar_redes=False,
                  pode_gerenciar_usuarios=False, somente_leitura=False):
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("Nome do perfil e obrigatorio.")
    db = SessionLocal()
    try:
        if db.query(Perfil).filter_by(nome=nome).first():
            raise ValueError(f"Ja existe um perfil chamado '{nome}'.")
        perfil = Perfil(
            nome=nome, descricao=(descricao or "").strip(),
            pode_gerenciar_redes=bool(pode_gerenciar_redes),
            pode_gerenciar_usuarios=bool(pode_gerenciar_usuarios),
            somente_leitura=bool(somente_leitura),
        )
        db.add(perfil)
        db.commit()
        return _perfil_para_dict(perfil)
    finally:
        db.close()


def editar_perfil(perfil_id, nome=None, descricao=None, pode_gerenciar_redes=None,
                   pode_gerenciar_usuarios=None, somente_leitura=None):
    db = SessionLocal()
    try:
        perfil = db.get(Perfil, perfil_id)
        if not perfil:
            raise ValueError("Perfil nao encontrado.")
        if nome:
            perfil.nome = nome.strip()
        if descricao is not None:
            perfil.descricao = descricao.strip()
        if pode_gerenciar_redes is not None:
            perfil.pode_gerenciar_redes = bool(pode_gerenciar_redes)
        if pode_gerenciar_usuarios is not None:
            perfil.pode_gerenciar_usuarios = bool(pode_gerenciar_usuarios)
        if somente_leitura is not None:
            perfil.somente_leitura = bool(somente_leitura)
        db.commit()
        return _perfil_para_dict(perfil)
    finally:
        db.close()


def excluir_perfil(perfil_id):
    db = SessionLocal()
    try:
        perfil = db.get(Perfil, perfil_id)
        if not perfil:
            raise ValueError("Perfil nao encontrado.")
        if perfil.usuarios:
            raise ValueError(
                "Este perfil tem usuarios vinculados -- troque o perfil "
                "deles antes de excluir."
            )
        db.delete(perfil)
        db.commit()
    finally:
        db.close()


# ── Usuarios ─────────────────────────────────────────────────

def _usuario_para_dict(usuario):
    return {
        "id": usuario.id,
        "nome": usuario.nome,
        "email": usuario.email,
        "is_super_admin": usuario.is_super_admin,
        "acesso_total": usuario.acesso_total,
        "ativo": usuario.ativo,
        "totp_habilitado": usuario.totp_habilitado,
        "perfil_id": usuario.perfil_id,
        "perfil_nome": usuario.perfil.nome if usuario.perfil else None,
        "unidade_ids": [u.id for u in usuario.unidades],
        "rede_ids": [r.id for r in usuario.redes],
        "unidades_nomes": [u.nome for u in usuario.unidades],
        "redes_nomes": [r.nome for r in usuario.redes],
        "ultimo_login_em": usuario.ultimo_login_em.strftime("%Y-%m-%d %H:%M") if usuario.ultimo_login_em else None,
        "criado_em": usuario.criado_em.strftime("%Y-%m-%d %H:%M") if usuario.criado_em else None,
    }


def listar_usuarios():
    db = SessionLocal()
    try:
        usuarios = db.query(Usuario).order_by(Usuario.nome).all()
        return [_usuario_para_dict(u) for u in usuarios]
    finally:
        db.close()


def obter_usuario(usuario_id):
    db = SessionLocal()
    try:
        usuario = db.get(Usuario, usuario_id)
        if not usuario:
            raise ValueError("Usuario nao encontrado.")
        return _usuario_para_dict(usuario)
    finally:
        db.close()


def _aplicar_acesso(db, usuario, unidade_ids, rede_ids):
    usuario.unidades = (
        db.query(Unidade).filter(Unidade.id.in_(unidade_ids)).all() if unidade_ids else []
    )
    usuario.redes = (
        db.query(Rede).filter(Rede.id.in_(rede_ids)).all() if rede_ids else []
    )


def criar_usuario(nome, email, senha, perfil_id=None, acesso_total=False,
                   unidade_ids=None, rede_ids=None):
    nome = (nome or "").strip()
    email = (email or "").strip().lower()
    if not nome or not email or not senha:
        raise ValueError("Nome, e-mail e senha sao obrigatorios.")
    if len(senha) < 8:
        raise ValueError("A senha precisa ter pelo menos 8 caracteres.")

    db = SessionLocal()
    try:
        if db.query(Usuario).filter_by(email=email).first():
            raise ValueError(f"Ja existe um usuario com o e-mail '{email}'.")
        if perfil_id and not db.get(Perfil, perfil_id):
            raise ValueError("Perfil nao encontrado.")

        usuario = Usuario(
            nome=nome, email=email, senha_hash=gerar_hash_senha(senha),
            perfil_id=perfil_id or None, acesso_total=bool(acesso_total),
            is_super_admin=False, ativo=True,
        )
        db.add(usuario)
        db.flush()
        _aplicar_acesso(db, usuario, unidade_ids, rede_ids)
        db.commit()
        return _usuario_para_dict(usuario)
    finally:
        db.close()


def editar_usuario(usuario_id, nome=None, perfil_id=None, acesso_total=None,
                    unidade_ids=None, rede_ids=None, ativo=None, nova_senha=None):
    db = SessionLocal()
    try:
        usuario = db.get(Usuario, usuario_id)
        if not usuario:
            raise ValueError("Usuario nao encontrado.")
        if usuario.is_super_admin:
            raise ValueError("O super-admin nao e gerenciado por aqui.")

        if nome:
            usuario.nome = nome.strip()
        if perfil_id is not None:
            if perfil_id and not db.get(Perfil, perfil_id):
                raise ValueError("Perfil nao encontrado.")
            usuario.perfil_id = perfil_id or None
        if acesso_total is not None:
            usuario.acesso_total = bool(acesso_total)
        if unidade_ids is not None or rede_ids is not None:
            _aplicar_acesso(db, usuario, unidade_ids or [], rede_ids or [])
        if ativo is not None:
            usuario.ativo = bool(ativo)
        if nova_senha:
            if len(nova_senha) < 8:
                raise ValueError("A senha precisa ter pelo menos 8 caracteres.")
            usuario.senha_hash = gerar_hash_senha(nova_senha)

        db.commit()
        return _usuario_para_dict(usuario)
    finally:
        db.close()


def excluir_usuario(usuario_id):
    db = SessionLocal()
    try:
        usuario = db.get(Usuario, usuario_id)
        if not usuario:
            raise ValueError("Usuario nao encontrado.")
        if usuario.is_super_admin:
            raise ValueError("O super-admin nao pode ser excluido por aqui.")
        db.delete(usuario)
        db.commit()
    finally:
        db.close()


# ── Resolucao de permissao (usada por app.py/painel/routes.py) ─

def flags_de_perfil(usuario):
    if usuario.is_super_admin:
        return {"pode_gerenciar_redes": True, "pode_gerenciar_usuarios": True, "somente_leitura": False}
    if not usuario.perfil:
        return {"pode_gerenciar_redes": False, "pode_gerenciar_usuarios": False, "somente_leitura": True}
    return {
        "pode_gerenciar_redes": bool(usuario.perfil.pode_gerenciar_redes),
        "pode_gerenciar_usuarios": bool(usuario.perfil.pode_gerenciar_usuarios),
        "somente_leitura": bool(usuario.perfil.somente_leitura),
    }


def carregar_permissoes(usuario_id):
    """Retorna um dict com tudo que app.py/painel/routes.py precisam saber
    sobre o que esse usuario pode ver/fazer, resolvido de uma vez (perfil +
    escopo de acesso)."""
    db = SessionLocal()
    try:
        usuario = db.get(Usuario, usuario_id)
        if not usuario:
            return None
        flags = flags_de_perfil(usuario)
        return {
            "is_super_admin": usuario.is_super_admin,
            "acesso_total": usuario.acesso_total or usuario.is_super_admin,
            "unidade_ids": {u.id for u in usuario.unidades},
            "rede_ids": {r.id for r in usuario.redes},
            **flags,
        }
    finally:
        db.close()


def redes_visiveis_para(usuario_id):
    """Lista (resumida, sem segredos) das redes que esse usuario pode ver."""
    perm = carregar_permissoes(usuario_id)
    if not perm:
        return []
    todas = listar_redes()
    if perm["acesso_total"]:
        return todas
    return [
        r for r in todas
        if r["id"] in perm["rede_ids"] or r["unidade_id"] in perm["unidade_ids"]
    ]


def usuario_pode_acessar_rede(usuario_id, rede_id):
    perm = carregar_permissoes(usuario_id)
    if not perm:
        return False
    if perm["acesso_total"]:
        return True
    if rede_id in perm["rede_ids"]:
        return True
    try:
        rede = obter_rede(rede_id)
    except ValueError:
        return False
    return rede["unidade_id"] in perm["unidade_ids"]
