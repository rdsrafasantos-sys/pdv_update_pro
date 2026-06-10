# ===============================================================
#  config_server.py — Configurações do Servidor
#  Edite este arquivo para adicionar/remover lojas e PDVs
# ===============================================================

# Token de segurança — deve ser IGUAL ao configurado no agente.py
TOKEN_SEGURANCA = "pdv-agent-2024"

# Porta do servidor web (acesse via navegador)
PORTA_SERVIDOR = 8080

# Pasta onde os arquivos .zip de atualização ficam armazenados
UPLOAD_DIR = "/opt/pdv-server/uploads"

# ──────────────────────────────────────────────
# LOJAS E PDVs
# Adicione aqui todas as suas lojas e PDVs
# ──────────────────────────────────────────────
LOJAS = [
    {
        "id": "loja01",
        "nome": "Loja 01 - Centro",
        "pdvs": [
            {"id": "PDV-01", "nome": "Caixa 01", "ip": "192.168.1.101"},
            {"id": "PDV-02", "nome": "Caixa 02", "ip": "192.168.1.102"},
            {"id": "PDV-03", "nome": "Caixa 03", "ip": "192.168.1.103"},
            {"id": "PDV-04", "nome": "Caixa 04", "ip": "192.168.1.104"},
            {"id": "PDV-05", "nome": "Caixa 05", "ip": "192.168.1.105"},
        ]
    },
    {
        "id": "loja02",
        "nome": "Loja 02 - Shopping",
        "pdvs": [
            {"id": "PDV-01", "nome": "Caixa 01", "ip": "192.168.2.101"},
            {"id": "PDV-02", "nome": "Caixa 02", "ip": "192.168.2.102"},
            {"id": "PDV-03", "nome": "Caixa 03", "ip": "192.168.2.103"},
        ]
    },
    # Adicione mais lojas conforme necessário
]
