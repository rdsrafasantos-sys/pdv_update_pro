import os

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

# Token compartilhado com o servidor — sobrescreva via variável de ambiente
# PDV_AGENT_TOKEN em produção. O valor abaixo é apenas o default de
# desenvolvimento e deve ser igual ao PDV_SERVER_TOKEN configurado no servidor.
TOKEN_SEGURANCA = os.environ.get("PDV_AGENT_TOKEN", "pdv-agent-2024")

CONJUNTOS_SERVICOS = [
    ["MongoDumpRestore", "MongoFilho", "MongoStandalone"],
    ["MongoDireto", "MongoStandalone"],
]
