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
