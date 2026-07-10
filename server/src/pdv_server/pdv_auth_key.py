"""Persistência da auth key Tailscale para PDV terminals.

Prioridade de leitura:
1. AUTH_DATA_DIR/tailscale_pdv_key.json  (salvo via painel)
2. PDV_TAILSCALE_AUTH_KEY_PDV / PDV_TAILSCALE_AUTH_KEY_PDV_ID no ambiente

Isso permite configurar e atualizar a key via UI sem editar o .env
nem reiniciar o servidor.
"""
import json
import os

from pdv_server.config import AUTH_DATA_DIR, TAILSCALE_AUTH_KEY_PDV, TAILSCALE_AUTH_KEY_PDV_ID

_ARQUIVO = os.path.join(AUTH_DATA_DIR, "tailscale_pdv_key.json")


def ler() -> dict:
    """Retorna {'key': str, 'key_id': str}. Strings podem ser vazias."""
    if os.path.exists(_ARQUIVO):
        try:
            with open(_ARQUIVO, "r", encoding="utf-8") as f:
                dados = json.load(f)
            if dados.get("key"):
                return {"key": dados["key"], "key_id": dados.get("key_id", "")}
        except Exception:
            pass
    return {"key": TAILSCALE_AUTH_KEY_PDV, "key_id": TAILSCALE_AUTH_KEY_PDV_ID}


def salvar(key: str, key_id: str = "") -> None:
    os.makedirs(AUTH_DATA_DIR, exist_ok=True)
    with open(_ARQUIVO, "w", encoding="utf-8") as f:
        json.dump({"key": key.strip(), "key_id": key_id.strip()}, f, ensure_ascii=False)
