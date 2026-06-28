import io

import pyotp
import qrcode
import qrcode.image.svg
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()


def gerar_hash_senha(senha):
    return _hasher.hash(senha)


def verificar_senha(hash_armazenado, senha):
    try:
        return _hasher.verify(hash_armazenado, senha)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def precisa_rehash(hash_armazenado):
    """Permite migrar hashes antigos se os parametros do Argon2 mudarem."""
    try:
        return _hasher.check_needs_rehash(hash_armazenado)
    except Exception:
        return False


# ── 2FA (TOTP) ──────────────────────────────────────────────

def gerar_totp_secret():
    return pyotp.random_base32()


def totp_uri(secret, email, issuer="PDV Updater"):
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer)


def verificar_totp(secret, codigo):
    if not codigo:
        return False
    return pyotp.TOTP(secret).verify(codigo.strip(), valid_window=1)


def gerar_qr_svg(dados):
    """Gera o QR como <svg> "puro" (path unico, sem prefixo de namespace e
    sem o prolog <?xml ...?>) para poder ser embutido direto no HTML."""
    img = qrcode.make(dados, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    svg = buf.getvalue().decode()
    svg = svg.split("?>", 1)[-1].strip()  # remove o prolog <?xml ...?>
    return svg
