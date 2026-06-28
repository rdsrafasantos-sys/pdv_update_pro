from pdv_server.auth.models import Auditoria, SessionLocal


def registrar_auditoria(usuario_email, acao, detalhes="", ip=""):
    db = SessionLocal()
    try:
        db.add(Auditoria(
            usuario_email=usuario_email, acao=acao,
            detalhes=detalhes or "", ip=ip or "",
        ))
        db.commit()
    finally:
        db.close()


def listar_auditoria(limite=200):
    db = SessionLocal()
    try:
        registros = (
            db.query(Auditoria)
            .order_by(Auditoria.criado_em.desc())
            .limit(limite)
            .all()
        )
        return [
            {
                "id": r.id,
                "usuario_email": r.usuario_email,
                "acao": r.acao,
                "detalhes": r.detalhes,
                "ip": r.ip,
                "criado_em": r.criado_em.strftime("%Y-%m-%d %H:%M:%S") if r.criado_em else None,
            }
            for r in registros
        ]
    finally:
        db.close()
