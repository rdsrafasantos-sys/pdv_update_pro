import os
import sys

PORTA = int(os.environ.get("PDV_AGENT_PORTA", "5000"))

VRPDV_DIR = r"C:\vrpdv"
VRPDV_OLD_DIR = r"C:\vrpdv_old"
TEMP_ZIP = r"C:\vrpdv\_update.zip"
LMDB_PATH = r"C:\vrpdv\db\localdb"
DB_DIR = r"C:\vrpdv\db"
DB_TEMP_DIR = r"C:\PDVAgent\db_backup"
PROCESSOS = ["vrcheckout", "vrpdvapi"]

PASTA_AGENTE = r"C:\PDVAgent"
LOG_FILE = r"C:\PDVAgent\agente_pdv.log"
PROGRESSO_FILE = r"C:\PDVAgent\progresso.json"

_TOKEN_PROIBIDO = "pdv-agent-2024"

TOKEN_SEGURANCA = os.environ.get("PDV_AGENT_TOKEN", "")
if not TOKEN_SEGURANCA:
    sys.exit(
        "ERRO FATAL: PDV_AGENT_TOKEN nao definido. "
        "Configure a variavel de ambiente antes de iniciar o agente."
    )
if len(TOKEN_SEGURANCA) < 16:
    sys.exit(
        f"ERRO FATAL: PDV_AGENT_TOKEN muito curto ({len(TOKEN_SEGURANCA)} chars). "
        "Minimo: 16 caracteres."
    )
if TOKEN_SEGURANCA == _TOKEN_PROIBIDO:
    sys.exit(
        "ERRO FATAL: PDV_AGENT_TOKEN usa o valor padrao inseguro 'pdv-agent-2024'. "
        "Defina um token unico com: python -c \"import secrets; print(secrets.token_hex(32))\""
    )

CONJUNTOS_SERVICOS = [
    ["MongoDumpRestore", "MongoFilho", "MongoStandalone"],
    ["MongoDireto", "MongoStandalone"],
]
