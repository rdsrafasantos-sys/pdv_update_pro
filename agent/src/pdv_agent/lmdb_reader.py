import logging
import struct

from pdv_agent.config import LMDB_PATH

log = logging.getLogger("pdv_agent")

_info_pdv_cache = None


def ler_info_pdv():
    try:
        import lmdb
        env = lmdb.open(LMDB_PATH, readonly=True, lock=False, max_dbs=100)
        info = {}
        with env.begin() as txn:
            KEY_CONFIG = bytes.fromhex("1800000c00000001")
            value = txn.get(KEY_CONFIG)
            if value and len(value) >= 232:
                for p in range(100, len(value) - 12, 4):
                    v1 = struct.unpack_from("<i", value, p)[0]
                    gap = struct.unpack_from("<i", value, p + 4)[0]
                    v2 = struct.unpack_from("<i", value, p + 8)[0]
                    if 100 <= v1 <= 9999 and gap == 0 and 1 <= v2 <= 99:
                        info["numeroPdv"] = v1
                        info["idLoja"] = v2
                        log.info(f"numeroPdv={v1} idLoja={v2} na pos {p}")
                        break
        env.close()
        if info:
            log.info(f"Info PDV lida: {info}")
        else:
            log.warning("Nao foi possivel extrair info do LMDB")
        return info if info else None
    except ImportError:
        log.warning("lmdb nao instalado")
        return None
    except Exception as e:
        log.error(f"Erro ao ler LMDB: {e}")
        return None


def get_info_pdv():
    global _info_pdv_cache
    if _info_pdv_cache is None:
        _info_pdv_cache = ler_info_pdv()
    return _info_pdv_cache


def invalidar_cache_info_pdv():
    global _info_pdv_cache
    _info_pdv_cache = None
