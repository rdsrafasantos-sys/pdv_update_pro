# ===============================================================
#  config_server.py — Configurações do Servidor
# ===============================================================

# Token de segurança — deve ser IGUAL ao configurado no agente.py
TOKEN_SEGURANCA = "pdv-agent-2024"

# Porta do servidor web
PORTA_SERVIDOR = 8888

# Pasta de uploads dos .zip
UPLOAD_DIR = "/opt/pdv-server/uploads"

# ──────────────────────────────────────────────
# MONGODB — banco do integrador VR
# ──────────────────────────────────────────────
# Sem autenticação (padrão):
MONGO_URI = "mongodb://localhost:27016"

# Com autenticação (se necessário):
# MONGO_URI = "mongodb://usuario:senha@localhost:27016"
