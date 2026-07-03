"""Gera o script bash que a tela de Instalacao oferece para rodar no
service manager de um cliente novo (ponto 2/3 do onboarding automatizado).
O script roda inteiramente do lado do cliente: instala o Tailscale, conecta,
descobre as faixas de rede lendo o replica set do Mongo local (sem nmap --
ver discussao no painel/routes.py) e ativa o subnet router. So entao avisa
o painel via callback HTTP, que age sozinho dali pra frente (ACL/rotas)."""

SCRIPT_TEMPLATE = r"""#!/bin/bash
# Gerado automaticamente pela tela de Instalacao do PDV Updater.
# Site ID: {site_id} -- rode este script como root (ou com sudo) na
# maquina do service manager deste cliente (a que roda o integrador e o
# MongoDB na porta 27016). Nao precisa de nenhuma outra informacao --
# tudo que falta este script descobre sozinho.
set -uo pipefail

SITE_ID="{site_id}"
AUTH_KEY="{auth_key}"
ERP_IP="{erp_ip}"
CALLBACK_URL="{callback_url}"
HOSTNAME_TS="service-manager-{site_id}"

reportar() {{
  curl -s -m 10 -X POST -H "Content-Type: application/json" -d "$1" "$CALLBACK_URL" >/dev/null 2>&1 || true
}}

erro_fatal() {{
  reportar "{{\"status\":\"erro\",\"mensagem\":\"$1\"}}"
  echo "ERRO: $1" >&2
  exit 1
}}

echo "== Instalacao PDV Updater -- Site ID $SITE_ID =="
reportar '{{"status":"iniciando"}}'

# 1. Instala o Tailscale (idempotente -- nao reinstala se ja existir)
if ! command -v tailscale >/dev/null 2>&1; then
  echo "Instalando Tailscale..."
  curl -fsSL https://tailscale.com/install.sh | sh || erro_fatal "Falha ao instalar o Tailscale."
fi

# 2. Conecta -- a tag (tag:pdv-service-manager) ja vem da auth key, nao
# precisa de --advertise-tags aqui.
echo "Conectando ao Tailscale..."
tailscale up --authkey="$AUTH_KEY" --hostname="$HOSTNAME_TS" \
  || erro_fatal "Falha ao conectar ao Tailscale (auth key invalida/expirada?)."

TS_IP=$(tailscale ip -4 2>/dev/null | head -1)
[ -z "$TS_IP" ] && erro_fatal "Tailscale conectou mas nao retornou IP."
echo "Conectado. IP Tailscale: $TS_IP"
reportar "{{\"status\":\"conectado\",\"tailscale_ip\":\"$TS_IP\",\"tailscale_hostname\":\"$HOSTNAME_TS\"}}"

# 3. Descobre as faixas de rede lendo o replica set do Mongo LOCAL (porta
# 27016) -- e a mesma lista de membros que o painel ja usa para achar os
# PDVs, entao reflete a topologia real sem precisar varrer a rede.
echo "Lendo replica set do Mongo local..."
FAIXAS=""
if command -v mongosh >/dev/null 2>&1; then
  SAIDA=$(mongosh --quiet --eval 'rs.status().members.map(m => m.name).join(",")' "mongodb://localhost:27016" 2>/dev/null)
elif command -v mongo >/dev/null 2>&1; then
  SAIDA=$(mongo --quiet --eval 'print(rs.status().members.map(m => m.name).join(","))' "mongodb://localhost:27016" 2>/dev/null)
else
  SAIDA=""
fi

for HOST in $(echo "$SAIDA" | tr ',' ' '); do
  IP=$(echo "$HOST" | cut -d: -f1)
  FAIXA=$(echo "$IP" | awk -F. '{{print $1"."$2"."$3".0/24"}}')
  echo "$FAIXA" >> /tmp/.pdv_faixas_$$
done

if [ -n "$ERP_IP" ]; then
  FAIXA_ERP=$(echo "$ERP_IP" | awk -F. '{{print $1"."$2"."$3".0/24"}}')
  echo "$FAIXA_ERP" >> /tmp/.pdv_faixas_$$
fi

if [ -f /tmp/.pdv_faixas_$$ ]; then
  FAIXAS=$(sort -u /tmp/.pdv_faixas_$$ | paste -sd, -)
  rm -f /tmp/.pdv_faixas_$$
fi

if [ -z "$FAIXAS" ]; then
  erro_fatal "Nao foi possivel detectar nenhuma faixa de rede (replica set vazio/sem mongosh, e nenhum IP de ERP informado). Rode manualmente: tailscale debug via $SITE_ID <faixa>"
fi
echo "Faixas detectadas: $FAIXAS"

# 4. Gera os prefixos IPv6 (4via6), um por faixa
PREFIXOS=""
IFS=',' read -ra LISTA_FAIXAS <<< "$FAIXAS"
for FAIXA in "${{LISTA_FAIXAS[@]}}"; do
  PREFIXO=$(tailscale debug via "$SITE_ID" "$FAIXA" 2>/dev/null | tail -1 | tr -d '[:space:]')
  if [ -n "$PREFIXO" ]; then
    PREFIXOS="$PREFIXOS,$PREFIXO"
  fi
done
PREFIXOS="${{PREFIXOS#,}}"

[ -z "$PREFIXOS" ] && erro_fatal "Faixas detectadas ($FAIXAS) mas 'tailscale debug via' nao gerou nenhum prefixo."
echo "Prefixos 4via6: $PREFIXOS"

# 5. Ativa o subnet router com os prefixos gerados
tailscale set --advertise-routes="$PREFIXOS" \
  || erro_fatal "Falha ao ativar advertise-routes."

# 6. Libera trafego Tailscale para containers Docker (sem isso a porta 27016
# do integrador fica inacessivel pelo Tailscale mesmo com ACL correto,
# porque o Docker usa bridge networking com iptables proprios).
echo "Configurando iptables para Tailscale + Docker..."
iptables -I DOCKER-USER -i tailscale0 -j ACCEPT 2>/dev/null || true
if command -v netfilter-persistent >/dev/null 2>&1; then
  netfilter-persistent save 2>/dev/null || true
elif [ -d /etc/iptables ]; then
  iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
fi

echo "Concluido. Aguardando aprovacao das rotas e atualizacao do ACL pelo painel."
reportar "{{\"status\":\"concluido\",\"faixas\":\"$FAIXAS\",\"prefixos\":\"$PREFIXOS\",\"tailscale_ip\":\"$TS_IP\",\"tailscale_hostname\":\"$HOSTNAME_TS\"}}"
"""


def gerar_script(site_id, auth_key, erp_ip, callback_url):
    return SCRIPT_TEMPLATE.format(
        site_id=site_id,
        auth_key=auth_key,
        erp_ip=erp_ip or "",
        callback_url=callback_url,
    )
