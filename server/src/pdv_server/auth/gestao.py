"""CRUD de Unidades, Redes, Usuarios e Perfis (Fases 1 e 2), mais a
resolucao de permissao/RBAC. Segredos de cada rede (Mongo URI, token,
Tailscale Site ID) sao sempre cifrados antes de ir para o banco -- ver
auth/crypto.py. Quem usa: rotas em painel/routes.py e app.py.

A automacao do wizard de instalacao de clientes novos (pool de auth keys,
Site ID, script do service manager, callback, ACL) fica em
auth/gestao_instalacao.py -- assunto bem diferente que so coincidia aqui
por mexer nas mesmas tabelas."""
import logging
import re

from pdv_server.auth.crypto import cifrar, decifrar
from pdv_server.auth.models import Perfil, Rede, SessionLocal, Unidade, Usuario
from pdv_server.auth.security import gerar_hash_senha

log = logging.getLogger(__name__)

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
        "nome_fantasia": rede.nome_fantasia,
        "razao_social": rede.razao_social,
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
        redes = query.order_by(Rede.nome_fantasia).all()
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
        raise ValueError(f"Ja existe uma rede com este CNPJ: '{existente.nome_fantasia}'.")


_TOKEN_MINIMO = 16
_TOKEN_PROIBIDO = "pdv-agent-2024"


def _validar_token_agente(token):
    """Mesma regra que o agente (config.py) exige para PDV_AGENT_TOKEN --
    um token mais curto nunca vai conseguir autenticar em nenhum PDV."""
    if len(token) < _TOKEN_MINIMO:
        raise ValueError(
            f"Token muito curto ({len(token)} caracteres). "
            f"O agente dos PDVs exige no minimo {_TOKEN_MINIMO} caracteres."
        )
    if token == _TOKEN_PROIBIDO:
        raise ValueError("Nao use o valor padrao inseguro. Escolha um token unico.")


def criar_rede(nome_fantasia, unidade_id, mongo_uri, token, tailscale_site_id="", cnpj="", razao_social=""):
    nome_fantasia = (nome_fantasia or "").strip()
    mongo_uri = (mongo_uri or "").strip()
    token = (token or "").strip()
    if not nome_fantasia or not unidade_id or not mongo_uri or not token:
        raise ValueError("Nome fantasia, unidade, Mongo URI e token sao obrigatorios.")
    _validar_token_agente(token)
    cnpj_normalizado = validar_cnpj(cnpj) if cnpj else None

    db = SessionLocal()
    try:
        if not db.get(Unidade, unidade_id):
            raise ValueError("Unidade nao encontrada.")
        if db.query(Rede).filter_by(nome_fantasia=nome_fantasia).first():
            raise ValueError(f"Ja existe uma rede chamada '{nome_fantasia}'.")
        if cnpj_normalizado:
            _checar_cnpj_disponivel(db, cnpj_normalizado)

        rede = Rede(
            nome_fantasia=nome_fantasia,
            razao_social=(razao_social or "").strip() or None,
            unidade_id=unidade_id, cnpj=cnpj_normalizado,
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


def editar_rede(rede_id, nome_fantasia, unidade_id, mongo_uri, token, tailscale_site_id="", cnpj=None, razao_social=None):
    db = SessionLocal()
    try:
        rede = db.get(Rede, rede_id)
        if not rede:
            raise ValueError("Rede nao encontrada.")

        if nome_fantasia:
            rede.nome_fantasia = nome_fantasia.strip()
        if razao_social is not None:
            rede.razao_social = razao_social.strip() or None
        if unidade_id:
            if not db.get(Unidade, unidade_id):
                raise ValueError("Unidade nao encontrada.")
            rede.unidade_id = unidade_id
        if mongo_uri:
            rede.mongo_uri_cifrado = cifrar(mongo_uri.strip())
        if token:
            token = token.strip()
            _validar_token_agente(token)
            rede.token_cifrado = cifrar(token)
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

# ── Perfis ───────────────────────────────────────────────────

def _perfil_para_dict(perfil):
    return {
        "id": perfil.id,
        "nome": perfil.nome,
        "descricao": perfil.descricao,
        "pode_gerenciar_redes": bool(perfil.pode_gerenciar_redes),
        "pode_gerenciar_usuarios": bool(perfil.pode_gerenciar_usuarios),
        "somente_leitura": bool(perfil.somente_leitura),
        "pode_ver_fiscal": bool(perfil.pode_ver_fiscal),
        "pode_atu_agente": bool(perfil.pode_atu_agente),
        "pode_atu_pdv_upload": bool(perfil.pode_atu_pdv_upload),
        "pode_atu_pdv_disparar": bool(perfil.pode_atu_pdv_disparar),
        "pode_atu_pdv_limpar": bool(perfil.pode_atu_pdv_limpar),
        "pode_atu_integrador": bool(perfil.pode_atu_integrador),
        "pode_replic_verificar": bool(perfil.pode_replic_verificar),
        "pode_replic_config": bool(perfil.pode_replic_config),
        "pode_config_banco": bool(perfil.pode_config_banco),
        "pode_config_integrador": bool(perfil.pode_config_integrador),
        "pode_reenviar_documentos": bool(perfil.pode_reenviar_documentos),
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
                  pode_gerenciar_usuarios=False, somente_leitura=False,
                  pode_ver_fiscal=False,
                  pode_atu_agente=False, pode_atu_pdv_upload=False,
                  pode_atu_pdv_disparar=False, pode_atu_pdv_limpar=False,
                  pode_atu_integrador=False,
                  pode_replic_verificar=False, pode_replic_config=False,
                  pode_config_banco=False, pode_config_integrador=False,
                  pode_reenviar_documentos=False):
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
            pode_ver_fiscal=bool(pode_ver_fiscal),
            pode_atu_agente=bool(pode_atu_agente),
            pode_atu_pdv_upload=bool(pode_atu_pdv_upload),
            pode_atu_pdv_disparar=bool(pode_atu_pdv_disparar),
            pode_atu_pdv_limpar=bool(pode_atu_pdv_limpar),
            pode_atu_integrador=bool(pode_atu_integrador),
            pode_replic_verificar=bool(pode_replic_verificar),
            pode_replic_config=bool(pode_replic_config),
            pode_config_banco=bool(pode_config_banco),
            pode_config_integrador=bool(pode_config_integrador),
            pode_reenviar_documentos=bool(pode_reenviar_documentos),
        )
        db.add(perfil)
        db.commit()
        return _perfil_para_dict(perfil)
    finally:
        db.close()


def editar_perfil(perfil_id, nome=None, descricao=None, pode_gerenciar_redes=None,
                   pode_gerenciar_usuarios=None, somente_leitura=None,
                   pode_ver_fiscal=None,
                   pode_atu_agente=None, pode_atu_pdv_upload=None,
                   pode_atu_pdv_disparar=None, pode_atu_pdv_limpar=None,
                   pode_atu_integrador=None,
                   pode_replic_verificar=None, pode_replic_config=None,
                   pode_config_banco=None, pode_config_integrador=None,
                   pode_reenviar_documentos=None):
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
        if pode_ver_fiscal is not None:
            perfil.pode_ver_fiscal = bool(pode_ver_fiscal)
        for flag in ("pode_atu_agente", "pode_atu_pdv_upload", "pode_atu_pdv_disparar",
                     "pode_atu_pdv_limpar", "pode_atu_integrador",
                     "pode_replic_verificar", "pode_replic_config",
                     "pode_config_banco", "pode_config_integrador",
                     "pode_reenviar_documentos"):
            valor = locals()[flag]
            if valor is not None:
                setattr(perfil, flag, bool(valor))
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

_TODAS_FLAGS_SECAO = (
    "pode_ver_fiscal",
    "pode_atu_agente", "pode_atu_pdv_upload", "pode_atu_pdv_disparar",
    "pode_atu_pdv_limpar", "pode_atu_integrador",
    "pode_replic_verificar", "pode_replic_config",
    "pode_config_banco", "pode_config_integrador",
    "pode_reenviar_documentos",
)


def flags_de_perfil(usuario):
    if usuario.is_super_admin:
        base = {"pode_gerenciar_redes": True, "pode_gerenciar_usuarios": True, "somente_leitura": False}
        base.update({f: True for f in _TODAS_FLAGS_SECAO})
        return base
    if not usuario.perfil:
        base = {"pode_gerenciar_redes": False, "pode_gerenciar_usuarios": False, "somente_leitura": True}
        base.update({f: False for f in _TODAS_FLAGS_SECAO})
        return base
    base = {
        "pode_gerenciar_redes": bool(usuario.perfil.pode_gerenciar_redes),
        "pode_gerenciar_usuarios": bool(usuario.perfil.pode_gerenciar_usuarios),
        "somente_leitura": bool(usuario.perfil.somente_leitura),
    }
    base.update({f: bool(getattr(usuario.perfil, f, False)) for f in _TODAS_FLAGS_SECAO})
    return base


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
