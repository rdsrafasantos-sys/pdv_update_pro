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

# Site ID do Tailscale 4via6 (subnet router) para este cliente — necessario
# quando a rede interna do cliente usa IPs fixos que nao podem ser alterados
# (o replica set do Mongo referencia esse IP direto, ver discovery.py) e essa
# faixa pode colidir com a de outro cliente na mesma tailnet. Deixe em branco
# se este cliente usa Tailscale instalado direto em cada maquina (sem overlap
# possivel, ip do replica set já é o IP Tailscale) — comportamento padrao,
# sem nenhuma traducao de endereco.
PDV_TAILSCALE_SITE_ID = os.environ.get("PDV_TAILSCALE_SITE_ID", "")

# Onde ficam persistidos a configuracao de verificacao automatica e o historico
REPLICACAO_DATA_DIR = os.environ.get("PDV_REPLICACAO_DATA_DIR", "/opt/pdv-server/replicacao")

# ──────────────────────────────────────────────
# BANCO DE DADOS DO ERP — PostgreSQL (testado em "Configuracoes" no painel)
# ──────────────────────────────────────────────
# Onde fica persistida a configuracao de conexao com o banco do ERP
ERP_DB_DATA_DIR = os.environ.get("PDV_ERP_DB_DATA_DIR", "/opt/pdv-server/erp_db")

# ──────────────────────────────────────────────
# INTEGRADOR VR — testado em "Configuracoes" no painel
# ──────────────────────────────────────────────
# Onde fica persistida a configuracao de conexao com o integrador (ip/porta) e
# com o MongoDB que ele alimenta
INTEGRADOR_DATA_DIR = os.environ.get("PDV_INTEGRADOR_DATA_DIR", "/opt/pdv-server/integrador")

# ──────────────────────────────────────────────
# AUTENTICACAO / PAINEL ADMINISTRATIVO (usuarios, redes, auditoria)
# ──────────────────────────────────────────────
# Onde fica o banco SQLite local do painel (usuarios, unidades, redes,
# auditoria) — nao tem relacao com o MongoDB do integrador.
AUTH_DATA_DIR = os.environ.get("PDV_AUTH_DATA_DIR", "/opt/pdv-server/auth")

# Chave mestra usada para criptografar segredos em repouso (token/Mongo URI
# de cada rede) com Fernet. OBRIGATORIA em producao — gere uma com:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Sem ela definida, o processo nao sobe (ver app.py). Nunca commitar este
# valor — fica so no .env de cada instalacao.
MASTER_KEY = os.environ.get("PDV_MASTER_KEY", "")

# Chave de sessao do Flask (assinatura do cookie). Tambem obrigatoria em
# producao, gere com: python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY = os.environ.get("PDV_SECRET_KEY", "")

# ──────────────────────────────────────────────
# TAILSCALE API — automacao da tela de Instalacao (gerar auth key por
# instalacao, atualizar ACL com as faixas descobertas, aprovar rotas)
# ──────────────────────────────────────────────
# OAuth client gerado em Settings > OAuth clients no admin console do
# Tailscale, com escopos ACL (read+write), Auth Keys (write) e Devices
# (read+write) -- ver tailscale_api.py. Credencial poderosa: fica so no
# .env de cada instalacao, nunca commitada, nunca no banco.
TAILSCALE_OAUTH_CLIENT_ID = os.environ.get("PDV_TAILSCALE_OAUTH_CLIENT_ID", "")
TAILSCALE_OAUTH_CLIENT_SECRET = os.environ.get("PDV_TAILSCALE_OAUTH_CLIENT_SECRET", "")

# Identificador do tailnet na API ("-" funciona como "o tailnet deste token")
TAILSCALE_TAILNET = os.environ.get("PDV_TAILSCALE_TAILNET", "-")

# URL pela qual o script do service manager (ja conectado via Tailscale)
# consegue chamar de volta este painel -- IP ou hostname MagicDNS deste
# servidor na tailnet, com porta. Ex: http://100.112.37.21:8888
PAINEL_CALLBACK_URL = os.environ.get("PDV_PAINEL_CALLBACK_URL", "")
