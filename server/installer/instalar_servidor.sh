#!/bin/bash
# ===============================================================
#  instalar_servidor.sh
#  Execute no Service Manager Ubuntu para instalar o servidor.
#  Rode a partir da raiz do repo: server/installer/instalar_servidor.sh
# ===============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$(dirname "$SCRIPT_DIR")"
DEST=/opt/pdv-server

echo "=== Instalando PDV Server ==="

sudo mkdir -p "$DEST/uploads"
sudo cp -r "$SERVER_DIR"/. "$DEST/"
sudo chmod -R 755 "$DEST"

echo "Criando virtualenv e instalando dependencias..."
sudo python3 -m venv "$DEST/venv"
sudo "$DEST/venv/bin/pip" install --upgrade pip
sudo "$DEST/venv/bin/pip" install -r "$DEST/requirements.txt"

echo "Criando servico systemd..."
sudo tee /etc/systemd/system/pdv-server.service > /dev/null << EOF
[Unit]
Description=PDV Update Server
After=network.target

[Service]
WorkingDirectory=$DEST
ExecStart=$DEST/venv/bin/python $DEST/main.py
Restart=always
RestartSec=5
# Sobrescreva conforme necessario (token deve ser igual ao PDV_AGENT_TOKEN do agente):
# Environment=PDV_SERVER_TOKEN=troque-este-token
# Environment=PDV_SERVER_MONGO_URI=mongodb://localhost:27016
# Environment=PDV_SERVER_PORTA=8888

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable pdv-server
sudo systemctl restart pdv-server

echo ""
echo "=== PDV Server instalado! ==="
echo "Acesse: http://$(hostname -I | awk '{print $1}'):8888"
echo "Status: sudo systemctl status pdv-server"
