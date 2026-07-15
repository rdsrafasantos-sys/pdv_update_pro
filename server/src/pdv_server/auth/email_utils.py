import urllib.request
import urllib.error
import json

from pdv_server.config import RESEND_API_KEY, EMAIL_REMETENTE


def enviar_email_reset(email_destino: str, nome: str, token: str, painel_url: str) -> bool:
    if not RESEND_API_KEY:
        return False

    link = f"{painel_url.rstrip('/')}/redefinir-senha/{token}"
    html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px 24px;background:#111;color:#e5e5e5;border-radius:12px;">
      <div style="font-size:22px;font-weight:700;margin-bottom:8px;">
        PDV <span style="color:#ff7a1a">Updater</span>
      </div>
      <p style="color:#999;font-size:13px;margin-top:0;">Painel central de redes, lojas e PDVs</p>
      <hr style="border:none;border-top:1px solid #2a2a2a;margin:20px 0;">
      <p style="font-size:15px;">Olá, <strong>{nome}</strong>.</p>
      <p style="font-size:14px;color:#ccc;">
        Recebemos uma solicitação para redefinir a senha da sua conta.
        Clique no botão abaixo para criar uma nova senha.
      </p>
      <div style="text-align:center;margin:28px 0;">
        <a href="{link}" style="background:#ff7a1a;color:#1a0f00;font-weight:700;font-size:14px;
           padding:12px 28px;border-radius:8px;text-decoration:none;display:inline-block;">
          Redefinir minha senha
        </a>
      </div>
      <p style="font-size:12px;color:#666;">
        Este link expira em <strong>1 hora</strong>. Se você não solicitou a redefinição,
        pode ignorar este e-mail — sua senha permanece a mesma.
      </p>
      <hr style="border:none;border-top:1px solid #2a2a2a;margin:20px 0;">
      <p style="font-size:11px;color:#444;text-align:center;">PDV Updater · vrsoft.pdvproupdater.com.br</p>
    </div>
    """

    payload = json.dumps({
        "from": f"PDV Updater <{EMAIL_REMETENTE}>",
        "to": [email_destino],
        "subject": "Redefinição de senha — PDV Updater",
        "html": html,
    }).encode()

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 201)
    except urllib.error.HTTPError:
        return False
    except Exception:
        return False
