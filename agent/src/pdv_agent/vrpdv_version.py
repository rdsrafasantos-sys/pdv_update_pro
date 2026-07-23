"""Le a versao do VRPdvPro direto do metadado FileVersion do vrcheckout.exe.

Nao usamos o campo 'versao' que o proprio VRPdvPro grava no MongoDB (pdv.pdvs)
porque, na pratica, esse campo so e reescrito por alguma acao administrativa
esporadica -- foi observado meses desatualizado mesmo apos atualizacoes reais
via painel e reaberturas de caixa. O FileVersion embutido no executavel
reflete o binario de fato presente em disco, sempre correto.
"""
import logging
import os

from pdv_agent.config import VRPDV_DIR

log = logging.getLogger("pdv_agent")

VRCHECKOUT_EXE = os.path.join(VRPDV_DIR, "vrcheckout.exe")


def ler_versao_vrpdv():
    if not os.path.exists(VRCHECKOUT_EXE):
        return None
    try:
        import pefile
        pe = pefile.PE(VRCHECKOUT_EXE, fast_load=True)
        try:
            pe.parse_data_directories(
                directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]]
            )
            for fileinfo in getattr(pe, "FileInfo", []):
                for entry in fileinfo:
                    if entry.Key != b"StringFileInfo":
                        continue
                    for tabela in entry.StringTable:
                        for chave, valor in tabela.entries.items():
                            if chave == b"FileVersion":
                                return valor.decode(errors="replace").strip()
            return None
        finally:
            # pefile faz memory-map do arquivo no Windows (mmap.mmap) -- sem
            # fechar explicitamente, o mapeamento pode ficar aberto tempo
            # suficiente pra colidir com uma atualizacao concorrente tentando
            # sobrescrever o mesmo vrcheckout.exe (OSError Errno 22).
            pe.close()
    except Exception as e:
        log.warning(f"Nao foi possivel ler versao do vrcheckout.exe: {e}")
        return None
