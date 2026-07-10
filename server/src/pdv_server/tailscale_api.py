"""Cliente da API REST do Tailscale, usado pela automacao da tela de
Instalacao (painel/routes.py + auth/gestao.py) para gerar auth keys por
instalacao, acrescentar prefixos IPv6 no ACL e aprovar rotas de subnet
router -- sem precisar entrar manualmente no admin console a cada cliente
novo. Credencial (OAuth client) configurada via PDV_TAILSCALE_OAUTH_CLIENT_ID/
SECRET, ver config.py. Sem ela configurada, AutomacaoIndisponivel e levantada
e quem chamou decide o fallback (mostrar comando manual, por exemplo)."""
import time

import requests

from pdv_server.config import (
    PAINEL_CALLBACK_URL, TAILSCALE_OAUTH_CLIENT_ID,
    TAILSCALE_OAUTH_CLIENT_SECRET, TAILSCALE_TAILNET,
)

_BASE_URL = "https://api.tailscale.com/api/v2"

_token_cache = {"valor": None, "expira_em": 0}


class AutomacaoIndisponivel(Exception):
    """Credencial da API do Tailscale nao configurada neste servidor --
    quem chamou deve cair para o fluxo manual (mostrar o comando)."""


def automacao_disponivel():
    return bool(TAILSCALE_OAUTH_CLIENT_ID and TAILSCALE_OAUTH_CLIENT_SECRET)


def _token():
    if not automacao_disponivel():
        raise AutomacaoIndisponivel(
            "PDV_TAILSCALE_OAUTH_CLIENT_ID/SECRET nao configurados neste servidor."
        )
    agora = time.time()
    if _token_cache["valor"] and agora < _token_cache["expira_em"]:
        return _token_cache["valor"]

    resposta = requests.post(
        f"{_BASE_URL}/oauth/token",
        data={
            "client_id": TAILSCALE_OAUTH_CLIENT_ID,
            "client_secret": TAILSCALE_OAUTH_CLIENT_SECRET,
            "grant_type": "client_credentials",
        },
        timeout=15,
    )
    resposta.raise_for_status()
    dados = resposta.json()
    _token_cache["valor"] = dados["access_token"]
    # Renova um pouco antes de expirar de fato (folga de 60s)
    _token_cache["expira_em"] = agora + int(dados.get("expires_in", 3600)) - 60
    return _token_cache["valor"]


def _get(caminho, **kwargs):
    r = requests.get(f"{_BASE_URL}{caminho}", auth=(_token(), ""), timeout=20, **kwargs)
    r.raise_for_status()
    return r


def _post(caminho, json=None, **kwargs):
    r = requests.post(f"{_BASE_URL}{caminho}", auth=(_token(), ""), json=json, timeout=20, **kwargs)
    r.raise_for_status()
    return r


def criar_auth_key(tags, descricao, expiry_seconds=3600 * 24, reusable=False):
    """Gera uma auth key Tailscale marcada com as tags informadas.
    reusable=False (padrão) cria key de uso único — ideal para service manager
    onde cada instalação tem sua própria key. reusable=True gera key que pode
    ser usada em múltiplos dispositivos — ideal para instalar vários PDVs."""
    corpo = {
        "capabilities": {
            "devices": {
                "create": {
                    "reusable": reusable,
                    "ephemeral": False,
                    "preauthorized": True,
                    "tags": tags,
                }
            }
        },
        "expirySeconds": expiry_seconds,
        "description": descricao,
    }
    dados = _post(f"/tailnet/{TAILSCALE_TAILNET}/keys", json=corpo).json()
    return dados["key"]


def obter_acl():
    """Le o ACL atual em JSON estruturado (sem comentarios -- a API so
    devolve os comentarios no formato HuJSON puro, que nao da pra editar
    programaticamente de forma segura). Editar via automacao tem esse
    custo: comentarios escritos a mao no admin console se perdem na
    proxima vez que a automacao salvar de volta."""
    return _get(f"/tailnet/{TAILSCALE_TAILNET}/acl", headers={"Accept": "application/json"}).json()


def salvar_acl(politica):
    _post(f"/tailnet/{TAILSCALE_TAILNET}/acl", json=politica)


def adicionar_prefixos_ao_grant(tag_dst, prefixos):
    """Acrescenta prefixos IPv6 (4via6) na lista `dst` do grant cujo dst
    ja contem `tag_dst` (ex: tag:pdv-service-manager) -- sem duplicar os
    que ja estiverem la, sem tocar no resto da politica."""
    politica = obter_acl()
    grant_encontrado = False
    for grant in politica.get("grants", []):
        if tag_dst in grant.get("dst", []):
            existentes = set(grant["dst"])
            grant["dst"] = list(existentes | set(prefixos))
            grant_encontrado = True
    if not grant_encontrado:
        raise ValueError(f"Nenhum grant com dst contendo '{tag_dst}' foi encontrado no ACL.")
    salvar_acl(politica)


def obter_info_key(key_id: str) -> dict:
    """Retorna metadados de uma auth key pelo ID (kXXXXXXXXXX).
    Inclui 'expires' (ISO 8601) e 'invalid' (bool)."""
    return _get(f"/tailnet/{TAILSCALE_TAILNET}/keys/{key_id}").json()


def listar_dispositivos():
    return _get(f"/tailnet/{TAILSCALE_TAILNET}/devices", params={"fields": "all"}).json()["devices"]


def obter_dispositivo_por_hostname(hostname):
    for dispositivo in listar_dispositivos():
        if dispositivo.get("hostname") == hostname:
            return dispositivo
    return None


def aprovar_rotas(device_id, rotas):
    _post(f"/device/{device_id}/routes", json={"routes": rotas})


def callback_url_disponivel():
    return bool(PAINEL_CALLBACK_URL)
