"""Contexto de execucao por Rede (Fase 3 -- multi-tenant).

Cada chamada ao core (discovery/dispatch/replication/erp_db/integrador)
passa a receber um RedeContexto em vez de ler config.py global -- assim o
mesmo processo atende varias redes ao mesmo tempo, sem misturar dados.
"""
import os
from dataclasses import dataclass

from pdv_server.auth.gestao import obter_rede
from pdv_server.config import (
    ERP_DB_DATA_DIR, INTEGRADOR_DATA_DIR, REPLICACAO_DATA_DIR, UPLOAD_DIR,
)


@dataclass
class RedeContexto:
    rede_id: int
    nome: str
    mongo_uri: str
    token: str
    tailscale_site_id: str

    @property
    def upload_dir(self):
        return diretorio_rede(UPLOAD_DIR, self.rede_id)

    @property
    def replicacao_dir(self):
        return diretorio_rede(REPLICACAO_DATA_DIR, self.rede_id)

    @property
    def erp_db_dir(self):
        return diretorio_rede(ERP_DB_DATA_DIR, self.rede_id)

    @property
    def integrador_dir(self):
        return diretorio_rede(INTEGRADOR_DATA_DIR, self.rede_id)


def diretorio_rede(base_dir, rede_id):
    caminho = os.path.join(base_dir, str(rede_id))
    os.makedirs(caminho, exist_ok=True)
    return caminho


class RedeNaoEncontrada(Exception):
    pass


class RedeInativa(Exception):
    pass


def obter_contexto(rede_id, permitir_inativa=False):
    """Carrega a Rede do banco (com segredos decifrados) e monta o
    contexto usado pelo core. Lanca RedeNaoEncontrada/RedeInativa."""
    try:
        dados = obter_rede(rede_id, com_segredos=True)
    except ValueError:
        raise RedeNaoEncontrada(f"Rede {rede_id} nao encontrada.")

    if not dados["ativa"] and not permitir_inativa:
        raise RedeInativa(f"Rede '{dados['nome_fantasia']}' esta desativada.")

    return RedeContexto(
        rede_id=dados["id"],
        nome=dados["nome_fantasia"],
        mongo_uri=dados["mongo_uri"],
        token=dados["token"],
        tailscale_site_id=dados.get("tailscale_site_id") or "",
    )
