from functools import wraps
from urllib.parse import urlparse

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
from pdv_server.auth.email_utils import enviar_email_reset
from pdv_server.auth.gestao import flags_de_perfil
from pdv_server.auth.models import ResetSenha, SessionLocal, Usuario
from pdv_server.auth.security import (
    gerar_hash_senha, gerar_qr_svg, gerar_totp_secret, totp_uri, verificar_senha,
    verificar_totp,
)
from pdv_server.config import PAINEL_URL_PUBLICA

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Faça login para continuar."

limiter = Limiter(key_func=get_remote_address)

auth_bp = Blueprint("auth", __name__)


def _ip_cliente():
    # request.remote_addr ja vem correto -- ProxyFix (app.py) so reescreve
    # a partir de X-Forwarded-For em producao, onde ha proxy reverso
    # confiavel na frente. Ler o cabecalho aqui direto voltaria a confiar
    # num valor que qualquer cliente pode forjar.
    return request.remote_addr or ""


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


def _url_segura(url, fallback):
    """Aceita apenas caminhos relativos neste servidor (sem scheme nem netloc)."""
    if not url:
        return fallback
    parsed = urlparse(url)
    if parsed.scheme or parsed.netloc:
        return fallback
    return url


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

        proximo_login = _url_segura(request.form.get("proximo"), url_for("painel.redes"))
        if usuario.totp_habilitado:
            session["pre_2fa_usuario_id"] = usuario.id
            session["pre_2fa_proximo"] = proximo_login
            registrar_auditoria(usuario.email, "login_aguardando_2fa", ip=ip)
            return redirect(url_for("auth.verificar_2fa"))

        login_user(UsuarioLogado(usuario))
        registrar_auditoria(usuario.email, "login_sucesso", detalhes="sem 2FA habilitado", ip=ip)
        return redirect(proximo_login)
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
        proximo = _url_segura(session.pop("pre_2fa_proximo", None), url_for("painel.redes"))
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


@auth_bp.route("/api/2fa/setup", methods=["GET"])
@login_required
def api_2fa_setup():
    """Gera novo segredo TOTP e retorna QR SVG para o usuário atual."""
    db = SessionLocal()
    try:
        usuario = db.get(Usuario, int(current_user.id))
        if usuario.totp_habilitado:
            return jsonify({"erro": "2FA já está habilitado. Peça ao administrador para redefinir."})
        segredo = gerar_totp_secret()
        session["novo_totp_secret"] = segredo
        uri = totp_uri(segredo, usuario.email)
        qr_svg = gerar_qr_svg(uri)
        return jsonify({"segredo": segredo, "qr_svg": qr_svg})
    finally:
        db.close()


@auth_bp.route("/api/2fa/confirmar", methods=["POST"])
@login_required
def api_2fa_confirmar():
    """Verifica o código TOTP e habilita o 2FA para o usuário atual."""
    codigo = ((request.json or {}).get("codigo") or "").strip()
    segredo = session.get("novo_totp_secret")
    if not segredo:
        return jsonify({"erro": "Sessão expirada. Gere um novo QR code."})
    if not verificar_totp(segredo, codigo):
        return jsonify({"erro": "Código inválido. Verifique o aplicativo autenticador."})
    db = SessionLocal()
    try:
        usuario = db.get(Usuario, int(current_user.id))
        usuario.totp_secret_cifrado = cifrar(segredo)
        usuario.totp_habilitado = True
        db.commit()
        session.pop("novo_totp_secret", None)
        registrar_auditoria(usuario.email, "2fa_habilitado", ip=_ip_cliente())
        return jsonify({"ok": True})
    finally:
        db.close()


@auth_bp.route("/api/usuarios/<int:usuario_id>/reset-2fa", methods=["POST"])
@login_required
def api_reset_2fa(usuario_id):
    """Super-admin redefine o 2FA de qualquer usuário."""
    if not current_user.is_super_admin:
        return jsonify({"erro": "Apenas super-admin pode redefinir o 2FA de outros usuários."}), 403
    db = SessionLocal()
    try:
        usuario = db.get(Usuario, usuario_id)
        if not usuario:
            return jsonify({"erro": "Usuário não encontrado."}), 404
        usuario.totp_habilitado = False
        usuario.totp_secret_cifrado = None
        db.commit()
        registrar_auditoria(
            current_user.email, "2fa_redefinido",
            detalhes=f"para {usuario.email}", ip=_ip_cliente(),
        )
        return jsonify({"ok": True})
    finally:
        db.close()


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    registrar_auditoria(current_user.email, "logout", ip=_ip_cliente())
    logout_user()
    return redirect(url_for("auth.login"))


@auth_bp.route("/recuperar-senha", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def recuperar_senha():
    if current_user.is_authenticated:
        return redirect(url_for("painel.redes"))

    if request.method == "GET":
        return render_template("recuperar_senha.html")

    email = (request.form.get("email") or "").strip().lower()
    # Sempre exibe a mesma mensagem para não revelar se o e-mail existe
    msg_generica = "Se este e-mail estiver cadastrado, você receberá um link em instantes."

    db = SessionLocal()
    try:
        usuario = db.query(Usuario).filter_by(email=email, ativo=True).first()
        if usuario:
            # Invalida tokens anteriores deste usuário
            db.query(ResetSenha).filter_by(usuario_id=usuario.id, usado=False).update({"usado": True})
            token = secrets.token_hex(32)
            expira_em = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
            db.add(ResetSenha(usuario_id=usuario.id, token=token, expira_em=expira_em))
            db.commit()
            enviar_email_reset(usuario.email, usuario.nome, token, PAINEL_URL_PUBLICA)
            registrar_auditoria(usuario.email, "reset_senha_solicitado", ip=_ip_cliente())
    finally:
        db.close()

    flash(msg_generica, "ok")
    return redirect(url_for("auth.recuperar_senha"))


@auth_bp.route("/redefinir-senha/<token>", methods=["GET", "POST"])
def redefinir_senha(token):
    if current_user.is_authenticated:
        return redirect(url_for("painel.redes"))

    db = SessionLocal()
    try:
        agora = datetime.datetime.utcnow()
        reset = db.query(ResetSenha).filter_by(token=token, usado=False).first()

        if not reset or reset.expira_em < agora:
            flash("Link inválido ou expirado. Solicite um novo.", "erro")
            return redirect(url_for("auth.recuperar_senha"))

        if request.method == "GET":
            return render_template("redefinir_senha.html", token=token)

        nova_senha = request.form.get("senha") or ""
        confirmar = request.form.get("confirmar") or ""

        if len(nova_senha) < 8:
            flash("A senha deve ter pelo menos 8 caracteres.", "erro")
            return render_template("redefinir_senha.html", token=token)

        if nova_senha != confirmar:
            flash("As senhas não coincidem.", "erro")
            return render_template("redefinir_senha.html", token=token)

        usuario = db.get(Usuario, reset.usuario_id)
        usuario.senha_hash = gerar_hash_senha(nova_senha)
        reset.usado = True
        db.commit()
        registrar_auditoria(usuario.email, "reset_senha_concluido", ip=_ip_cliente())
        flash("Senha redefinida com sucesso. Faça login.", "ok")
        return redirect(url_for("auth.login"))
    finally:
        db.close()
