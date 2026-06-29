# PDV Updater

Sistema de atualização remota para PDVs (pontos de venda), composto por dois componentes independentes:

- **`agent/`** — roda em cada PDV (Windows). É um serviço Windows (`agente.exe`, via NSSM) que recebe `.zip`/`.exe` do servidor e executa a atualização local, mais um app de status (`status_pdv.exe`) que roda na sessão do usuário e mostra o progresso.
- **`server/`** — roda no Service Manager (Ubuntu). Painel web que descobre os PDVs automaticamente via replica set do MongoDB e dispara atualizações para um ou mais PDVs.

## Estrutura

```
agent/
  src/pdv_agent/     pacote Python do agente (config, leitura LMDB, controle de serviços, fluxo de update, rotas Flask, UI de status)
  main_agent.py       entrypoint do serviço (compilado como agente.exe)
  main_status.py      entrypoint da UI de status (compilado como status_pdv.exe)
  build_agent.spec     spec do PyInstaller para os dois executáveis
  installer/           instalador NSIS + script PowerShell
server/
  src/pdv_server/      pacote Python do servidor (config, descoberta MongoDB, despacho de updates, rotas Flask)
    auth/                login, 2FA, modelos (usuarios/redes/auditoria), criptografia de segredos
    seed_admin.py         cria o primeiro super-admin (linha de comando, uma vez)
  main.py               entrypoint do servidor
  Dockerfile / docker-compose.yml   instalação via Docker (recomendado)
  installer/             instalador Docker (recomendado) + script systemd legado
```

## Configuração via variáveis de ambiente

Nenhum segredo deve ser commitado. Os componentes leem config de variáveis de ambiente, com defaults de desenvolvimento:

| Variável | Componente | Default | Descrição |
|---|---|---|---|
| `PDV_AGENT_TOKEN` | agent | `pdv-agent-2024` | Token compartilhado — deve ser igual ao `PDV_SERVER_TOKEN` |
| `PDV_AGENT_PORTA` | agent | `5000` | Porta do agente |
| `PDV_SERVER_TOKEN` | server | `pdv-agent-2024` | Token compartilhado |
| `PDV_SERVER_PORTA` | server | `8888` | Porta do painel web |
| `PDV_SERVER_UPLOAD_DIR` | server | `/opt/pdv-server/uploads` | Onde ficam os `.zip`/`agente.exe` enviados |
| `PDV_SERVER_MONGO_URI` | server | `mongodb://localhost:27016` | Conexão com o MongoDB do integrador VR |
| `PDV_REPLICACAO_DB` | server | `pdv` | Banco que contém as coleções replicadas (mesmo nome nos dois lados) |
| `PDV_LOCAL_MONGO_PORTA` | server | `27018` | Porta do MongoDB local de cada PDV, acessado pelo IP do PDV |
| `PDV_REPLICACAO_DATA_DIR` | server | `/opt/pdv-server/replicacao` | Onde fica a config da verificação automática e o histórico |
| `PDV_MASTER_KEY` | server | _(obrigatória, sem default)_ | Chave Fernet para cifrar segredos em repouso (Mongo URI/token de cada rede). O processo não sobe sem ela. |
| `PDV_SECRET_KEY` | server | _(obrigatória, sem default)_ | Chave de assinatura da sessão (cookie de login). O processo não sobe sem ela. |
| `PDV_AUTH_DATA_DIR` | server | `/opt/pdv-server/auth` | Onde fica o banco SQLite do painel (usuários, redes, auditoria) |

Rodando via Docker (recomendado), `PDV_SERVER_TOKEN` e `PDV_SERVER_MONGO_URI` são lidos do `server/.env` (ver `.env.example`); os diretórios (`UPLOAD_DIR`, `REPLICACAO_DATA_DIR`, etc.) já vêm mapeados como volumes no `docker-compose.yml` e não precisam ser sobrescritos.

Em produção, defina `PDV_AGENT_TOKEN`/`PDV_SERVER_TOKEN` com um valor forte e igual nos dois lados (no agente isso normalmente é feito configurando a variável de ambiente do serviço NSSM; no servidor rodando via Docker, num arquivo `server/.env` — ver `server/.env.example` e a seção "Server — instalação" abaixo).

## Autenticação e segurança do painel

O painel inteiro fica atrás de login (sessão via `flask-login`, sem nenhuma
rota acessível sem autenticação além de `/login` e `/login/2fa`). Dados de
usuário (senha, segredos de cada rede, auditoria) ficam num banco SQLite
próprio em `PDV_AUTH_DATA_DIR`, separado do MongoDB do integrador.

- **Senhas**: hash com Argon2id (`argon2-cffi`), nunca texto puro.
- **2FA (TOTP)**: opcional por usuário, mas fortemente recomendado — ative em
  "⚠️ Configurar 2FA" no topo do painel após o primeiro login. Compatível com
  Google Authenticator, Authy, 1Password, etc.
- **Rate limit**: `/login` e `/login/2fa` aceitam no máximo 8 tentativas por
  minuto por IP (`flask-limiter`), para dificultar força-bruta.
- **Auditoria**: toda tentativa de login (sucesso/falha), 2FA e logout é
  registrada (usuário, ação, IP, data/hora) — consulta via
  `pdv_server.auth.audit.listar_auditoria()` (tela própria no painel é uma
  fase futura).
- **Segredos em repouso**: Mongo URI/token de cada rede são cifrados com
  Fernet (`cryptography`) usando `PDV_MASTER_KEY` antes de ir para o banco —
  nunca gravados em texto puro.

### Usuários e Perfis (RBAC) — tela `/usuarios`

Cada usuário tem um **Perfil** (capacidades: `pode_gerenciar_redes`,
`pode_gerenciar_usuarios`, `somente_leitura`) e um **escopo de acesso**
(`acesso_total`, ou listas específicas de Unidades/Redes). O super-admin
(criado via `seed_admin`) ignora tudo isso e sempre vê/pode tudo — é o
"break glass" da conta, não um perfil.

- `/redes` e toda a API `/api/<rede_id>/...` só mostram/permitem o que o
  usuário tem acesso (`auth/gestao.py: redes_visiveis_para`,
  `usuario_pode_acessar_rede`) — confirmado nos testes: usuário sem acesso a
  uma rede recebe 403 na API e é redirecionado para `/redes` na tela.
- Perfil com `somente_leitura=True` bloqueia qualquer ação de escrita
  (atualizar PDV/agente, reiniciar Mongo, disparar replicação, salvar
  configurações) com 403, mas continua lendo normalmente.
- Cadastro de Unidades continua restrito a super-admin (estrutura interna da
  VR Software); cadastro de Redes é liberado para quem tem
  `pode_gerenciar_redes`, limitado às Unidades que esse usuário já acessa.

### Primeiro acesso (criar o super-admin)

O primeiro usuário (super-admin) é criado por linha de comando, uma única
vez (o comando se recusa a rodar se já existir algum usuário) — depois
disso, use a tela `/usuarios` para cadastrar os demais:

```bash
# dentro do container (instalação via Docker):
docker compose exec pdv-server python -m pdv_server.seed_admin
# ou passando os dados direto: python -m pdv_server.seed_admin email senha "Nome"
```

O `instalar_servidor_docker.sh` já chama isso automaticamente na primeira
instalação.

## Multi-tenant: Unidades e Redes (Fase 3)

Um único painel atende várias **Redes** (clientes), cada uma pertencente a
uma **Unidade** (filial da VR Software) — ver telas `/redes` e `/unidades`.
Cada Rede tem seu próprio Mongo URI/token/Tailscale Site ID (cifrados no
banco, cadastrados pela tela, não mais em `.env`) e seus próprios dados
(`uploads/<rede_id>/`, `replicacao/<rede_id>/`, etc. — `contexto.py`).

- O painel de uma rede fica em `/r/<rede_id>/` (mesma tela de sempre — Dashboard,
  Atualização de Agente/PDV, Check Replicação, Configurações — só que escopada).
- Toda rota de API é prefixada: `/api/<rede_id>/...`.
- `PDV_SERVER_MONGO_URI`/`PDV_SERVER_TOKEN`/`PDV_TAILSCALE_SITE_ID` no `.env`
  **não são mais lidos em tempo de execução** — servem só de origem pro
  script de migração único (próximo item). Para uma rede nova, cadastre
  direto na tela `/redes`.

**Migrar uma instalação antiga (anterior à Fase 3) para o novo modelo:**

```bash
docker compose exec -e PYTHONPATH=/opt/pdv-server/src pdv-server \
  python -m pdv_server.migrar_rede_unica "Nome da Unidade" "Nome da Rede"
```

Cria a Unidade/Rede a partir do `.env` atual e move os dados já existentes
(`uploads`, histórico de replicação, config do ERP/integrador) para dentro
da pasta daquela rede. Só funciona se ainda não existir nenhuma rede
cadastrada (não reexecutar).

## Verificação de replicação

O painel web compara, sob demanda ou em agenda configurável, as coleções `pessoas`, `produtos`, `produtoscodigobarras`, `produtosimpostos`, `promocoes`, `promocoesconnectsimdepor`, `promocoesdepor` e `promocoeslevepor` entre o MongoDB da integradora (Mongo URI cadastrado na Rede) e o MongoDB local de cada PDV (`<ip-do-pdv>:PDV_LOCAL_MONGO_PORTA`). Isso exige que o Service Manager tenha rota de rede livre até essa porta em cada PDV.

A comparação é por documento completo (via `_id`), reportando:
- **faltando no PDV** — não replicou;
- **extras no PDV** — existe no PDV mas não na integradora;
- **alterados** — existe nos dois lados mas o conteúdo difere.

Por ser uma comparação pesada (baixa as coleções inteiras dos dois lados), ela só roda quando disparada manualmente (botão no painel, por PDV selecionado) ou pela verificação automática configurável (`/api/replicacao/config`: habilitada, intervalo em minutos, todos os PDVs ou uma lista). O resultado de cada execução automática fica no histórico (`/api/replicacao/historico`), exibido no painel — não há envio de notificação externa (e-mail/webhook) nesta versão.

## Agent — build e instalação (Windows)

```powershell
cd agent
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-build.txt
python -m PyInstaller build_agent.spec
```

Isso gera `dist/agente.exe` e `dist/status_pdv.exe`. Para instalar em um PDV:

- **Via NSIS**: copie os dois `.exe` + `nssm.exe` para `agent/installer/`, compile com `makensis PDVAgent_Setup.nsi` e distribua o instalador gerado.
- **Via PowerShell**: copie os `.exe` para a mesma pasta de `instalar_servico.ps1` e execute como Administrador.

## Server — instalação (Ubuntu, via Docker — recomendado)

Em qualquer servidor novo (cliente diferente, máquina diferente), com Docker
instalado ou não:

```bash
curl -fsSL https://raw.githubusercontent.com/rdsrafasantos-sys/pdv_update_pro/main/server/installer/instalar_servidor_docker.sh | bash
```

O script instala o Docker se necessário, clona o repositório, pergunta o
IP/porta do MongoDB do integrador *desse* cliente e o token compartilhado com
os agentes (grava em `server/.env`, não versionado), e sobe o container já
configurado. Pode ser executado de novo no mesmo servidor para atualizar
(faz `git pull` + rebuild, sem perder dados — uploads, histórico e config
ficam em `server/data/`, fora do container).

Comandos úteis após instalado (dentro de `~/pdv_update_pro/server`):
- `docker compose logs -f` — acompanhar logs
- `docker compose down` — parar
- `docker compose up -d --build` — atualizar depois de um `git pull`

### Instalação antiga via systemd (legado)

```bash
git clone <repo>
cd pdv-updater
./server/installer/instalar_servidor.sh
```

Mantido apenas para referência/rollback — cria um virtualenv em
`/opt/pdv-server/venv` e registra o serviço `pdv-server` no systemd, sem
Docker. Não é mais o caminho recomendado para instalações novas.

Para desenvolvimento local sem instalar como serviço nem Docker:

```bash
cd server
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Tailscale (VPN entre servidor e PDVs)

Cada rede de cliente (ex: `TEST`, `BONNA`) é isolada das outras na mesma tailnet
via um par de tags: `tag:pdv-<rede>` e `tag:server-<rede>`. O ACL (Access
Controls, no admin console do Tailscale) só libera tráfego entre o par de tags
de uma mesma rede — PDVs de redes diferentes nunca se enxergam. Ver o ACL atual
comentado no admin console; para adicionar uma rede nova, copie o par de tags
em `tagOwners` e o bloco de 2 `grants` correspondente. Lojas/PDVs novos
*dentro* de uma rede já existente não exigem mudança no ACL.

O instalador do agente (`PDVAgent_Setup.nsi`) tem uma tela opcional que pede a
auth key da rede do cliente e instala/conecta o Tailscale automaticamente
(`tailscale up --auth-key=... --unattended`). Requer o MSI oficial do
Tailscale em `agent/installer/tailscale-setup-amd64.msi` (baixe em
https://pkgs.tailscale.com/stable/#windows — não é versionado no git).

**Antes de distribuir uma auth key**: confira no admin console (Settings →
Keys) que ela foi gerada como **reutilizável**, **não-efêmera** e com a
**tag certa** (`tag:pdv-<rede>`) marcada — é fácil esquecer o checkbox da tag
ao gerar uma key, e nesse caso o PDV conecta mas fica sem tag, sem acesso a
nada (nem ao próprio servidor).

**Depois de qualquer mudança de tag/ACL em produção**: a propagação da nova
política pode não valer imediatamente para conexões já estabelecidas — rode
`sudo systemctl restart tailscaled` no servidor para forçar a releitura.
Isso reescreve regras de `iptables`, o que por sua vez pode quebrar o
port-forward do Docker para o `pdv-server` — em seguida rode também
`sudo systemctl restart docker` e confirme com `curl localhost:8888`.

### Clientes com IP interno fixo (replica set do Mongo não pode mudar)

Instalar o Tailscale direto em cada PDV/integrador (acima) só funciona quando
você controla a identidade de rede de cada máquina desde o início. Em
clientes existentes, o replica set do Mongo já referencia o IP interno da
loja (`192.168.x.x`) e **isso nunca pode ser alterado** (`discovery.py`,
`descobrir_pdvs_via_replicaset()`, le esse IP direto de
`replSetGetStatus()`). Nesse caso, em vez de instalar Tailscale em cada
máquina, uma única máquina dentro da rede do cliente (pode ser a do
integrador) atua como **subnet router**, expondo a faixa toda sem mudar
nenhum IP existente:

```bash
# Gera o prefixo IPv6 para cada faixa interna do cliente, com um Site ID
# UNICO por cliente (necessario pois faixas como 192.168.1.0/24 se repetem
# entre clientes diferentes — sem isso colide na mesma tailnet):
tailscale debug via 7 192.168.1.0/24
tailscale debug via 7 192.168.2.0/24

# Anuncia as rotas (substitua pelos prefixos gerados acima):
tailscale set --advertise-routes=<prefixo1>,<prefixo2> --advertise-tags=tag:server-bonna
```

Depois, no admin console: **aprovar a rota** (Machines → device → routes) e
**adicionar o(s) prefixo(s) IPv6 gerado(s)** (não o CIDR IPv4 original!) ao
`dst` do grant que libera esse cliente — esse foi o erro mais fácil de
cometer durante os testes. No lado de quem vai *consumir* a rota (o
`pdv-server`), é preciso `sudo tailscale set --accept-routes` — sem isso o
Tailscale recebe a rota mas não a usa, e parece que nada está configurado
quando na verdade só falta esse passo.

Cadastre o mesmo Site ID usado no `tailscale debug via` no campo "Tailscale
Site ID" da Rede (tela `/redes`, fica cifrado no banco) — a partir disso,
todo o código (`dispatch.py`, `replication.py`, `app.py`) traduz
automaticamente o IP bruto do replica set para o formato MagicDNS exigido
(`endereco_alcancavel()` em `discovery.py`), sem precisar mudar nada na UI —
o IP exibido no painel continua sendo o IP real, só a chamada de rede em si
usa o endereço traduzido. Redes sem esse campo preenchido continuam
funcionando normalmente (IP direto, sem tradução).

## Versionamento

A versão do agente fica em `agent/src/pdv_agent/__init__.py` (`VERSION`) e é exposta em `/ping`. Ao lançar uma nova versão, atualize também `!define VERSAO` em `agent/installer/PDVAgent_Setup.nsi` para manter o instalador consistente.

**Regra**: toda mudança de código no agente (não cosmética/instalador) deve vir acompanhada de um bump de `VERSION`/`VERSAO` — mesmo que pequena (ex: uma rota nova). Sem isso, `/ping` e o painel continuam reportando a versão antiga, dando a falsa impressão de que um PDV ainda não recebeu um recurso/fix que já está rodando nele.
