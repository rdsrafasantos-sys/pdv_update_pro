from cryptography.fernet import Fernet, InvalidToken

from pdv_server.config import MASTER_KEY

_fernet_instance = None


def _fernet():
    global _fernet_instance
    if _fernet_instance is None:
        if not MASTER_KEY:
            raise RuntimeError(
                "PDV_MASTER_KEY nao configurada. Gere uma com: "
                "python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\""
            )
        _fernet_instance = Fernet(MASTER_KEY.encode())
    return _fernet_instance


def cifrar(texto):
    if texto is None or texto == "":
        return None
    return _fernet().encrypt(texto.encode()).decode()


def decifrar(texto_cifrado):
    if texto_cifrado is None or texto_cifrado == "":
        return None
    try:
        return _fernet().decrypt(texto_cifrado.encode()).decode()
    except InvalidToken:
        raise ValueError("Nao foi possivel decifrar — PDV_MASTER_KEY incorreta ou dado corrompido.")
