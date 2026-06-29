"""CRUD de Unidades, Redes, Usuarios e Perfis (Fases 1 e 2). Segredos de
cada rede (Mongo URI, token, Tailscale Site ID) sao sempre cifrados antes
de ir para o banco -- ver auth/crypto.py. Quem usa: rotas em
painel/routes.py e app.py (resolucao de permissao)."""
from pdv_server.auth.crypto import cifrar, decifrar
from pdv_server.auth.models import Perfil, Rede, SessionLocal, Unidade, Usuario
from pdv_server.auth.security import gerar_hash_senha


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


def criar_rede(nome, unidade_id, mongo_uri, token, tailscale_site_id=""):
    nome = (nome or "").strip()
    mongo_uri = (mongo_uri or "").strip()
    token = (token or "").strip()
    if not nome or not unidade_id or not mongo_uri or not token:
        raise ValueError("Nome, unidade, Mongo URI e token sao obrigatorios.")

    db = SessionLocal()
    try:
        if not db.get(Unidade, unidade_id):
            raise ValueError("Unidade nao encontrada.")
        if db.query(Rede).filter_by(nome=nome).first():
            raise ValueError(f"Ja existe uma rede chamada '{nome}'.")

        rede = Rede(
            nome=nome, unidade_id=unidade_id,
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


def editar_rede(rede_id, nome, unidade_id, mongo_uri, token, tailscale_site_id=""):
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
