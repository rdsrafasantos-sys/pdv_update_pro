from functools import wraps

from flask import (
    Blueprint, current_app, flash, jsonify, redirect, render_template,
    request, session, url_for,
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import (
    LoginManager, UserMixin, current_user, login_required, login_user,
    logout_user,
)

from pdv_server.auth.audit import registrar_auditoria
from pdv_server.auth.crypto import cifrar, decifrar
from pdv_server.auth.gestao import flags_de_perfil
from pdv_server.auth.models import SessionLocal, Usuario
from pdv_server.auth.security import (
    gerar_qr_svg, gerar_totp_secret, totp_uri, verificar_senha,
    verificar_totp,
)

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Faça login para continuar."

limiter = Limiter(key_func=get_remote_address)

auth_bp = Blueprint("auth", __name__)


def _ip_cliente():
    return request.headers.get("X-Forwarded-For", request.remote_addr or "")


def exigir_super_admin(view):
    """So permite a view se o usuario logado for super-admin -- usado nas
    poucas coisas que ficam restritas mesmo com RBAC (gestao de Unidades)."""
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_super_admin:
            if request.path.startswith("/api/"):
                return jsonify({"erro": "Apenas super-admin pode fazer isso"}), 403
            return redirect(url_for("painel.redes"))
        return view(*args, **kwargs)
    return wrapper


def exigir_permissao(flag):
    """Decorator factory: exigir_permissao('pode_gerenciar_redes') etc.
    Usa as flags resolvidas em UsuarioLogado (perfil + super-admin)."""
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(*args, **kwargs):
            if not getattr(current_user, flag, False):
                if request.path.startswith("/api/"):
                    return jsonify({"erro": "Sem permissao para isso"}), 403
                return redirect(url_for("painel.redes"))
            return view(*args, **kwargs)
        return wrapper
    return decorator


def exigir_escrita(view):
    """Bloqueia mutacoes (POST/PUT/DELETE) para usuarios com perfil
    somente_leitura. Super-admin nunca tem essa flag (ver flags_de_perfil)."""
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if getattr(current_user, "somente_leitura", False):
            return jsonify({"erro": "Seu perfil e somente leitura"}), 403
        return view(*args, **kwargs)
    return wrapper


class UsuarioLogado(UserMixin):
    def __init__(self, usuario):
        self.id = str(usuario.id)
        self.email = usuario.email
        self.nome = usuario.nome
        self.is_super_admin = usuario.is_super_admin
        self.totp_habilitado = usuario.totp_habilitado
        self.acesso_total = bool(usuario.acesso_total) or usuario.is_super_admin
        self.unidade_ids = {u.id for u in usuario.unidades}
        self.rede_ids = {r.id for r in usuario.redes}
        for chave, valor in flags_de_perfil(usuario).items():
            setattr(self, chave, valor)


@login_manager.user_loader
def carregar_usuario(user_id):
    db = SessionLocal()
    try:
        usuario = db.get(Usuario, int(user_id))
        if usuario and usuario.ativo:
            return UsuarioLogado(usuario)
        return None
    finally:
        db.close()


@login_manager.unauthorized_handler
def nao_autorizado():
    if request.path.startswith("/api/"):
        return jsonify({"erro": "Autenticacao necessaria"}), 401
    return redirect(url_for("auth.login", proximo=request.path))


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("8 per minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("painel.redes"))

    if request.method == "GET":
        return render_template("login.html")

    email = (request.form.get("email") or "").strip().lower()
    senha = request.form.get("senha") or ""
    ip = _ip_cliente()

    db = SessionLocal()
    try:
        usuario = db.query(Usuario).filter_by(email=email).first()

        if not usuario or not usuario.ativo or not verificar_senha(usuario.senha_hash, senha):
            registrar_auditoria(email, "login_falhou", ip=ip)
            flash("E-mail ou senha invalidos.", "erro")
            return render_template("login.html"), 401

        if usuario.totp_habilitado:
            session["pre_2fa_usuario_id"] = usuario.id
            session["pre_2fa_proximo"] = request.form.get("proximo") or url_for("painel.redes")
            registrar_auditoria(usuario.email, "login_aguardando_2fa", ip=ip)
            return redirect(url_for("auth.verificar_2fa"))

        login_user(UsuarioLogado(usuario))
        registrar_auditoria(usuario.email, "login_sucesso", detalhes="sem 2FA habilitado", ip=ip)
        return redirect(request.form.get("proximo") or url_for("painel.redes"))
    finally:
        db.close()


@auth_bp.route("/login/2fa", methods=["GET", "POST"])
@limiter.limit("8 per minute", methods=["POST"])
def verificar_2fa():
    usuario_id = session.get("pre_2fa_usuario_id")
    if not usuario_id:
        return redirect(url_for("auth.login"))

    if request.method == "GET":
        return render_template("login_2fa.html")

    codigo = request.form.get("codigo") or ""
    ip = _ip_cliente()

    db = SessionLocal()
    try:
        usuario = db.get(Usuario, usuario_id)
        if not usuario or not usuario.ativo:
            session.pop("pre_2fa_usuario_id", None)
            return redirect(url_for("auth.login"))

        segredo = decifrar(usuario.totp_secret_cifrado)
        if not verificar_totp(segredo, codigo):
            registrar_auditoria(usuario.email, "2fa_falhou", ip=ip)
            flash("Codigo invalido.", "erro")
            return render_template("login_2fa.html"), 401

        session.pop("pre_2fa_usuario_id", None)
        proximo = session.pop("pre_2fa_proximo", url_for("painel.redes"))
        login_user(UsuarioLogado(usuario))
        registrar_auditoria(usuario.email, "login_sucesso", detalhes="com 2FA", ip=ip)
        return redirect(proximo)
    finally:
        db.close()


@auth_bp.route("/2fa/configurar", methods=["GET", "POST"])
@login_required
def configurar_2fa():
    db = SessionLocal()
    try:
        usuario = db.get(Usuario, int(current_user.id))

        if request.method == "GET":
            if usuario.totp_habilitado:
                return render_template("configurar_2fa.html", ja_habilitado=True)
            segredo = gerar_totp_secret()
            session["novo_totp_secret"] = segredo
            uri = totp_uri(segredo, usuario.email)
            qr_svg = gerar_qr_svg(uri)
            return render_template(
                "configurar_2fa.html", ja_habilitado=False,
                segredo=segredo, qr_svg=qr_svg,
            )

        codigo = request.form.get("codigo") or ""
        segredo = session.get("novo_totp_secret")
        if not segredo or not verificar_totp(segredo, codigo):
            flash("Codigo invalido. Tente escanear o QR novamente.", "erro")
            return redirect(url_for("auth.configurar_2fa"))

        usuario.totp_secret_cifrado = cifrar(segredo)
        usuario.totp_habilitado = True
        db.commit()
        session.pop("novo_totp_secret", None)
        registrar_auditoria(usuario.email, "2fa_habilitado", ip=_ip_cliente())
        flash("2FA habilitado com sucesso.", "ok")
        return redirect(url_for("painel.redes"))
    finally:
        db.close()


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    registrar_auditoria(current_user.email, "logout", ip=_ip_cliente())
    logout_user()
    return redirect(url_for("auth.login"))
