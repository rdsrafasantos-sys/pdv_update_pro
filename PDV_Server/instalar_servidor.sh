#!/bin/bash
# ===============================================================
#  instalar_servidor.sh
#  Execute no Service Manager Ubuntu para instalar o servidor
# ===============================================================

echo "=== Instalando PDV Server ==="

# Criar pasta do servidor
sudo mkdir -p /opt/pdv-server/uploads
sudo cp -r . /opt/pdv-server/
sudo chmod -R 755 /opt/pdv-server/

# Instalar dependências Python
echo "Instalando dependências..."
pip3 install flask requests pymongo --break-system-packages 2>/dev/null || \
pip3 install flask requests pymongo --user

# Criar serviço systemd para o servidor rodar automaticamente
echo "Criando serviço systemd..."
sudo tee /etc/systemd/system/pdv-server.service > /dev/null << 'EOF'
[Unit]
Description=PDV Update Server
After=network.target

[Service]
WorkingDirectory=/opt/pdv-server
ExecStart=/usr/bin/python3 /opt/pdv-server/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable pdv-server
sudo systemctl start pdv-server

echo ""
echo "=== PDV Server instalado! ==="
echo "Acesse: http://$(hostname -I | awk '{print $1}'):8080"
echo "Status: sudo systemctl status pdv-server"
