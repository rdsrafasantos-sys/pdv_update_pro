"""CRUD de Unidades e Redes (Fase 2). Segredos de cada rede (Mongo URI,
token, Tailscale Site ID) sao sempre cifrados antes de ir para o banco --
ver auth/crypto.py. Quem usa: rotas em painel/routes.py."""
from pdv_server.auth.crypto import cifrar, decifrar
from pdv_server.auth.models import Rede, SessionLocal, Unidade


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
