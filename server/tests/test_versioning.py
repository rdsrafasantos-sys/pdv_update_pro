"""Testes de versioning.eh_downgrade -- a regra que bloqueia atualizar um
PDV para uma versao mais antiga da que ja esta rodando (risco de corromper
o banco, conforme o comentario original em app.py)."""
from pdv_server.versioning import comparar_versoes, eh_downgrade, extrair_versao


def test_eh_downgrade_versao_menor_e_downgrade():
    assert eh_downgrade("7.0.5", "7.1.0") is True


def test_eh_downgrade_versao_maior_nao_e_downgrade():
    assert eh_downgrade("7.1.0", "7.0.5") is False


def test_eh_downgrade_mesma_versao_nao_e_downgrade():
    assert eh_downgrade("7.1.0", "7.1.0") is False


def test_eh_downgrade_aceita_prefixo_v():
    assert eh_downgrade("v7.0.5", "v7.1.0") is True
    assert eh_downgrade("v7.1.0", "v7.0.5") is False


def test_eh_downgrade_numero_de_digitos_diferente():
    # "7.1" e "7.1.0" sao a mesma versao (padding com zero) -- nao e downgrade
    assert eh_downgrade("7.1", "7.1.0") is False
    assert eh_downgrade("7.0", "7.1.0") is True


def test_eh_downgrade_sem_informacao_nao_bloqueia():
    assert eh_downgrade("", "7.1.0") is False
    assert eh_downgrade("7.1.0", "") is False
    assert eh_downgrade(None, "7.1.0") is False


def test_eh_downgrade_versao_nao_numerica_nao_bloqueia():
    assert eh_downgrade("abc", "7.1.0") is False


def test_extrair_versao_do_nome_arquivo():
    assert extrair_versao("VRPdvPro_7.1.0.zip") == "7.1.0"
    assert extrair_versao("agente_v2.3.1_final.exe") == "2.3.1"
    assert extrair_versao("sem_versao.zip") is None


def test_comparar_versoes():
    assert comparar_versoes("7.0.0", "7.1.0") == -1
    assert comparar_versoes("7.1.0", "7.0.0") == 1
    assert comparar_versoes("7.1.0", "7.1.0") == 0
