"""Testes de auth.gestao.usuario_pode_acessar_rede / redes_visiveis_para --
a checagem de isolamento multi-tenant usada por @com_rede em toda rota
/api/<rede_id>/... (ver app.py). carregar_permissoes/listar_redes/obter_rede
sao mockados para testar so a logica de decisao, sem tocar em banco real."""
from unittest.mock import patch

from pdv_server.auth import gestao


def _perm(acesso_total=False, rede_ids=None, unidade_ids=None):
    return {
        "acesso_total": acesso_total,
        "rede_ids": set(rede_ids or []),
        "unidade_ids": set(unidade_ids or []),
    }


@patch.object(gestao, "listar_redes")
@patch.object(gestao, "carregar_permissoes")
def test_redes_visiveis_para_acesso_total_ve_tudo(mock_perm, mock_listar):
    mock_perm.return_value = _perm(acesso_total=True)
    mock_listar.return_value = [{"id": 1, "unidade_id": 10}, {"id": 2, "unidade_id": 20}]
    assert gestao.redes_visiveis_para(usuario_id=1) == mock_listar.return_value


@patch.object(gestao, "listar_redes")
@patch.object(gestao, "carregar_permissoes")
def test_redes_visiveis_para_filtra_por_rede_ou_unidade(mock_perm, mock_listar):
    mock_perm.return_value = _perm(rede_ids=[2], unidade_ids=[10])
    mock_listar.return_value = [
        {"id": 1, "unidade_id": 10},  # unidade bate
        {"id": 2, "unidade_id": 99},  # rede_id bate
        {"id": 3, "unidade_id": 99},  # nenhum dos dois -- nao deve aparecer
    ]
    resultado = gestao.redes_visiveis_para(usuario_id=1)
    assert {r["id"] for r in resultado} == {1, 2}


@patch.object(gestao, "carregar_permissoes")
def test_redes_visiveis_para_sem_permissao_retorna_vazio(mock_perm):
    mock_perm.return_value = None
    assert gestao.redes_visiveis_para(usuario_id=999) == []


@patch.object(gestao, "obter_rede")
@patch.object(gestao, "carregar_permissoes")
def test_usuario_pode_acessar_rede_via_rede_id_direto(mock_perm, mock_obter):
    mock_perm.return_value = _perm(rede_ids=[5])
    assert gestao.usuario_pode_acessar_rede(usuario_id=1, rede_id=5) is True
    mock_obter.assert_not_called()


@patch.object(gestao, "obter_rede")
@patch.object(gestao, "carregar_permissoes")
def test_usuario_pode_acessar_rede_via_unidade(mock_perm, mock_obter):
    mock_perm.return_value = _perm(unidade_ids=[10])
    mock_obter.return_value = {"id": 5, "unidade_id": 10}
    assert gestao.usuario_pode_acessar_rede(usuario_id=1, rede_id=5) is True


@patch.object(gestao, "obter_rede")
@patch.object(gestao, "carregar_permissoes")
def test_usuario_pode_acessar_rede_nega_sem_relacao(mock_perm, mock_obter):
    # Regressao direta do achado da auditoria: usuario de uma unidade nao
    # pode acessar rede de outra unidade so porque tem a mesma permissao de perfil.
    mock_perm.return_value = _perm(rede_ids=[1], unidade_ids=[1])
    mock_obter.return_value = {"id": 5, "unidade_id": 99}
    assert gestao.usuario_pode_acessar_rede(usuario_id=1, rede_id=5) is False


@patch.object(gestao, "carregar_permissoes")
def test_usuario_pode_acessar_rede_sem_permissao_nega(mock_perm):
    mock_perm.return_value = None
    assert gestao.usuario_pode_acessar_rede(usuario_id=1, rede_id=5) is False


@patch.object(gestao, "obter_rede")
@patch.object(gestao, "carregar_permissoes")
def test_usuario_pode_acessar_rede_id_inexistente_nega(mock_perm, mock_obter):
    mock_perm.return_value = _perm(unidade_ids=[10])
    mock_obter.side_effect = ValueError("Rede nao encontrada.")
    assert gestao.usuario_pode_acessar_rede(usuario_id=1, rede_id=999) is False
