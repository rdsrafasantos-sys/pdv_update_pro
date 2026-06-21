import os

# Token de seguranca — deve ser IGUAL ao PDV_AGENT_TOKEN configurado no agente.
# Sobrescreva via variavel de ambiente PDV_SERVER_TOKEN em producao.
TOKEN_SEGURANCA = os.environ.get("PDV_SERVER_TOKEN", "pdv-agent-2024")

# Porta do servidor web
PORTA_SERVIDOR = int(os.environ.get("PDV_SERVER_PORTA", "8888"))

# Pasta de uploads dos .zip
UPLOAD_DIR = os.environ.get("PDV_SERVER_UPLOAD_DIR", "/opt/pdv-server/uploads")

# ──────────────────────────────────────────────
# MONGODB — banco do integrador VR
# ──────────────────────────────────────────────
# Sem autenticacao (padrao): mongodb://localhost:27016
# Com autenticacao: mongodb://usuario:senha@localhost:27016
MONGO_URI = os.environ.get("PDV_SERVER_MONGO_URI", "mongodb://localhost:27016")

# Nome do banco que contem as colecoes replicadas (igual nos dois lados)
REPLICACAO_DB = os.environ.get("PDV_REPLICACAO_DB", "pdv")

# Porta do MongoDB local de cada PDV (acessado pelo IP do PDV, ex: 192.168.x.x:27018)
PDV_LOCAL_MONGO_PORTA = int(os.environ.get("PDV_LOCAL_MONGO_PORTA", "27018"))

# Onde ficam persistidos a configuracao de verificacao automatica e o historico
REPLICACAO_DATA_DIR = os.environ.get("PDV_REPLICACAO_DATA_DIR", "/opt/pdv-server/replicacao")

# ──────────────────────────────────────────────
# BANCO DE DADOS DO ERP — PostgreSQL (testado em "Configuracoes" no painel)
# ──────────────────────────────────────────────
# Onde fica persistida a configuracao de conexao com o banco do ERP
ERP_DB_DATA_DIR = os.environ.get("PDV_ERP_DB_DATA_DIR", "/opt/pdv-server/erp_db")
