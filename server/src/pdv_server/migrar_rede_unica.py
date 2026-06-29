"""Migra a configuracao atual (.env: PDV_SERVER_MONGO_URI, PDV_SERVER_TOKEN,
PDV_TAILSCALE_SITE_ID) e os dados ja existentes (uploads, historico de
replicacao, config do ERP/integrador) para uma Rede cadastrada no banco
(Fase 3 -- antes disso tudo era "global", uma instalacao = uma rede).

So roda se ainda nao existir nenhuma Rede (evita migrar duas vezes).

Uso:
    python -m pdv_server.migrar_rede_unica "Nome da Unidade" "Nome da Rede"
"""
import os
import shutil
import sys

from pdv_server.auth.gestao import criar_rede, criar_unidade, listar_redes, listar_unidades
from pdv_server.auth.models import init_db
from pdv_server.config import (
    ERP_DB_DATA_DIR, INTEGRADOR_DATA_DIR, MONGO_URI, PDV_TAILSCALE_SITE_ID,
    REPLICACAO_DATA_DIR, TOKEN_SEGURANCA, UPLOAD_DIR,
)
from pdv_server.contexto import diretorio_rede


def _mover_arquivos(origem, destino, sufixos=None):
    if not os.path.isdir(origem):
        return 0
    movidos = 0
    for nome in os.listdir(origem):
        caminho = os.path.join(origem, nome)
        if not os.path.isfile(caminho):
            continue
        if sufixos and not any(nome.endswith(s) for s in sufixos):
            continue
        shutil.move(caminho, os.path.join(destino, nome))
        movidos += 1
    return movidos


def main():
    init_db()

    if listar_redes():
        print("Ja existe pelo menos uma rede cadastrada. Abortando "
              "(use a tela Redes para cadastrar novas).")
        sys.exit(1)

    nome_unidade = sys.argv[1] if len(sys.argv) > 1 else input("Nome da unidade: ").strip()
    nome_rede = sys.argv[2] if len(sys.argv) > 2 else input("Nome da rede: ").strip()

    if not nome_unidade or not nome_rede:
        print("Nome da unidade e nome da rede sao obrigatorios.")
        sys.exit(1)

    if not MONGO_URI or not TOKEN_SEGURANCA:
        print("PDV_SERVER_MONGO_URI / PDV_SERVER_TOKEN nao configurados no .env atual.")
        sys.exit(1)

    unidade_existente = next((u for u in listar_unidades() if u["nome"] == nome_unidade), None)
    unidade = unidade_existente or criar_unidade(nome_unidade)
    print(f"Unidade: {unidade['nome']} (id={unidade['id']})")

    rede = criar_rede(
        nome=nome_rede, unidade_id=unidade["id"],
        mongo_uri=MONGO_URI, token=TOKEN_SEGURANCA,
        tailscale_site_id=PDV_TAILSCALE_SITE_ID,
    )
    rede_id = rede["id"]
    print(f"Rede: {rede['nome']} (id={rede_id})")

    n_uploads = _mover_arquivos(UPLOAD_DIR, diretorio_rede(UPLOAD_DIR, rede_id))
    n_replic = _mover_arquivos(REPLICACAO_DATA_DIR, diretorio_rede(REPLICACAO_DATA_DIR, rede_id), sufixos=[".json"])
    n_erp = _mover_arquivos(ERP_DB_DATA_DIR, diretorio_rede(ERP_DB_DATA_DIR, rede_id), sufixos=[".json"])
    n_integ = _mover_arquivos(INTEGRADOR_DATA_DIR, diretorio_rede(INTEGRADOR_DATA_DIR, rede_id), sufixos=[".json"])

    print(f"Arquivos movidos -- uploads: {n_uploads}, replicacao: {n_replic}, "
          f"erp_db: {n_erp}, integrador: {n_integ}")
    print("")
    print(f"Pronto! Acesse: /r/{rede_id}/")


if __name__ == "__main__":
    main()
