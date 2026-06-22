import re

_VERSAO_RE = re.compile(r"(\d+(?:\.\d+){1,3})")


def extrair_versao(nome_arquivo):
    """Extrai a versao (ex: '7.1.0') do nome de um arquivo .zip de atualizacao.
    Retorna None se nenhum padrao de versao for encontrado no nome."""
    m = _VERSAO_RE.search(nome_arquivo)
    return m.group(1) if m else None


def versao_para_tupla(versao):
    return tuple(int(p) for p in versao.split("."))


def comparar_versoes(v1, v2):
    """Retorna -1 se v1 < v2, 0 se igual, 1 se v1 > v2."""
    t1, t2 = versao_para_tupla(v1), versao_para_tupla(v2)
    maxlen = max(len(t1), len(t2))
    t1 += (0,) * (maxlen - len(t1))
    t2 += (0,) * (maxlen - len(t2))
    if t1 < t2:
        return -1
    if t1 > t2:
        return 1
    return 0


def eh_downgrade(versao_zip, versao_pdv_atual):
    """True se aplicar versao_zip no PDV representaria um downgrade em relacao
    a versao_pdv_atual. Se alguma das versoes nao puder ser interpretada,
    retorna False (nao bloqueia por falta de informacao)."""
    if not versao_zip or not versao_pdv_atual:
        return False
    try:
        return comparar_versoes(versao_zip, versao_pdv_atual) < 0
    except ValueError:
        return False
