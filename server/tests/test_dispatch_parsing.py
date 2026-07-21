"""Testes de dispatch.extrair_payloads_de_log -- parser de log_api usado
pela feature de reenviar venda/NFC-e. Validado contra um log_api real de
cliente durante o desenvolvimento (ver historico de commits); estes testes
cobrem os casos de borda: bloco invalido, multiplos blocos, timestamp em
toda linha."""
from pdv_server.dispatch import extrair_payloads_de_log


def _bloco(json_str, timestamp="2026/07/03 10:15:13"):
    """Monta um bloco de log_api real: cada linha (inclusive as do corpo do
    JSON) prefixada com o timestamp, delimitada por --------- JSON ---------
    / --------- FIM JSON ---------."""
    corpo = "\n".join(f"{timestamp} {l}" for l in json_str.strip().splitlines())
    return (
        f"{timestamp} --------- JSON ---------\n"
        f"{corpo}\n"
        f"{timestamp} --------- FIM JSON ---------\n"
    )


def test_extrai_venda_simples():
    texto = _bloco('{"numeroCupom": 100, "pdv": 1, "idLoja": 2, "total": 10.5}')
    itens = extrair_payloads_de_log(texto)
    assert len(itens) == 1
    assert itens[0]["resumo"]["numeroCupom"] == 100
    assert itens[0]["resumo"]["valor"] == 10.5


def test_extrai_nfce_com_campos_reais_do_cliente():
    # Formato real reportado pelo usuario (chaveNFCe/emissaoNotaFiscalEmitente,
    # nao o NfceDTO documentado no Swagger) -- ver commit 2895d4a.
    texto = _bloco(
        '{"numeroCupom": 12507, "numeroPDV": 106, '
        '"chaveNFCe": "35260757144859000191651060000487311971855791", '
        '"valorNfe": "3.79", '
        '"emissaoNotaFiscalEmitente": {"idLoja": 1}}'
    )
    itens = extrair_payloads_de_log(texto)
    assert len(itens) == 1
    resumo = itens[0]["resumo"]
    assert resumo["idLoja"] == 1
    assert resumo["pdv"] == 106
    assert resumo["chaveNFCe"] == "35260757144859000191651060000487311971855791"


def test_ignora_bloco_sem_numero_cupom():
    texto = _bloco('{"outraCoisa": true}')
    assert extrair_payloads_de_log(texto) == []


def test_ignora_bloco_json_invalido():
    texto = (
        "2026/07/03 10:15:13 --------- JSON ---------\n"
        "2026/07/03 10:15:13 { isso nao eh json\n"
        "2026/07/03 10:15:13 --------- FIM JSON ---------\n"
    )
    assert extrair_payloads_de_log(texto) == []


def test_ignora_texto_sem_nenhum_bloco():
    assert extrair_payloads_de_log("2026/07/03 10:15:13 log qualquer sem JSON nenhum") == []


def test_extrai_multiplos_blocos_no_mesmo_arquivo():
    texto = (
        _bloco('{"numeroCupom": 1}')
        + "\n2026/07/03 10:16:00 linha de log qualquer no meio\n"
        + _bloco('{"numeroCupom": 2}', timestamp="2026/07/03 10:17:00")
    )
    itens = extrair_payloads_de_log(texto)
    assert [i["resumo"]["numeroCupom"] for i in itens] == [1, 2]


def test_payload_original_preservado_na_integra():
    texto = _bloco('{"numeroCupom": 1, "campoQualquer": {"a": [1, 2, 3]}}')
    itens = extrair_payloads_de_log(texto)
    assert itens[0]["payload"]["campoQualquer"] == {"a": [1, 2, 3]}
