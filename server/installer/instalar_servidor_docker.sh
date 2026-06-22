#!/bin/bash
# ===============================================================
#  instalar_servidor_docker.sh
#  Instala (ou atualiza) o PDV Server via Docker em um servidor novo.
#  Uso: baixe este arquivo e execute, ou rode direto:
#    curl -fsSL https://raw.githubusercontent.com/<repo>/main/server/installer/instalar_servidor_docker.sh | bash
#  Tambem pode ser executado de novo no mesmo servidor para atualizar
#  (puxa o codigo mais recente e reconstroi o container, sem perder dados).
# ===============================================================
set -e

REPO_URL="${PDV_REPO_URL:-https://github.com/rdsrafasantos-sys/pdv_update_pro.git}"
DEST="${PDV_INSTALL_DIR:-$HOME/pdv_update_pro}"

echo "=== Instalador do PDV Server (Docker) ==="
echo ""

# ── 1. Docker ──────────────────────────────────────────────
if ! command -v docker &> /dev/null; then
  echo "Docker nao encontrado. Instalando..."
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
  echo ""
  echo "Docker foi instalado agora. Saia e entre de novo na sessao SSH"
  echo "(ou rode 'newgrp docker') e execute este script novamente."
  exit 0
fi

if docker info &> /dev/null; then
  DC="docker compose"
else
  DC="sudo docker compose"
fi

if ! $DC version &> /dev/null; then
  echo "ERRO: plugin 'docker compose' nao encontrado. Atualize o Docker para a versao 20.10+."
  exit 1
fi

# ── 2. Codigo ───────────────────────────────────────────────
if [ -d "$DEST/.git" ]; then
  echo "Repositorio ja existe em $DEST, atualizando..."
  git -C "$DEST" pull
else
  echo "Clonando repositorio em $DEST..."
  git clone "$REPO_URL" "$DEST"
fi

cd "$DEST/server"

# ── 3. Configuracao deste servidor (.env) ──────────────────
if [ ! -f .env ]; then
  echo ""
  echo "--- Configuracao deste servidor ---"
  read -rp "IP/host do MongoDB do integrador (ex: 192.168.1.20): " MONGO_HOST
  read -rp "Porta do MongoDB do integrador [27016]: " MONGO_PORT
  MONGO_PORT="${MONGO_PORT:-27016}"
  read -rp "Token compartilhado com os agentes dos PDVs [pdv-agent-2024]: " TOKEN
  TOKEN="${TOKEN:-pdv-agent-2024}"

  cat > .env <<EOF
PDV_SERVER_MONGO_URI=mongodb://${MONGO_HOST}:${MONGO_PORT}
PDV_SERVER_TOKEN=${TOKEN}
EOF
  echo ".env criado em $DEST/server/.env"
else
  echo ""
  echo ".env ja existe, mantendo a configuracao atual ($DEST/server/.env)."
  echo "Para trocar o Mongo/token depois, edite esse arquivo e rode:"
  echo "  cd $DEST/server && $DC up -d --build"
fi

mkdir -p data/uploads data/replicacao data/erp_db data/integrador

# ── 4. Sobe o container ────────────────────────────────────
echo ""
echo "Construindo e iniciando o container..."
$DC up -d --build

IP=$(hostname -I | awk '{print $1}')
echo ""
echo "=============================="
echo "PDV Server instalado!"
echo "Acesse: http://${IP}:8888"
echo "Logs:   cd $DEST/server && $DC logs -f"
echo "Parar:  cd $DEST/server && $DC down"
echo "=============================="
