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

Rodando via Docker (recomendado), `PDV_SERVER_TOKEN` e `PDV_SERVER_MONGO_URI` são lidos do `server/.env` (ver `.env.example`); os diretórios (`UPLOAD_DIR`, `REPLICACAO_DATA_DIR`, etc.) já vêm mapeados como volumes no `docker-compose.yml` e não precisam ser sobrescritos.

Em produção, defina `PDV_AGENT_TOKEN`/`PDV_SERVER_TOKEN` com um valor forte e igual nos dois lados (no agente isso normalmente é feito configurando a variável de ambiente do serviço NSSM; no servidor rodando via Docker, num arquivo `server/.env` — ver `server/.env.example` e a seção "Server — instalação" abaixo).

## Verificação de replicação

O painel web compara, sob demanda ou em agenda configurável, as coleções `pessoas`, `produtos`, `produtoscodigobarras`, `produtosimpostos`, `promocoes`, `promocoesconnectsimdepor`, `promocoesdepor` e `promocoeslevepor` entre o MongoDB da integradora (`PDV_SERVER_MONGO_URI`) e o MongoDB local de cada PDV (`<ip-do-pdv>:PDV_LOCAL_MONGO_PORTA`). Isso exige que o Service Manager tenha rota de rede livre até essa porta em cada PDV.

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

## Versionamento

A versão do agente fica em `agent/src/pdv_agent/__init__.py` (`VERSION`) e é exposta em `/ping`. Ao lançar uma nova versão, atualize também `!define VERSAO` em `agent/installer/PDVAgent_Setup.nsi` para manter o instalador consistente.

**Regra**: toda mudança de código no agente (não cosmética/instalador) deve vir acompanhada de um bump de `VERSION`/`VERSAO` — mesmo que pequena (ex: uma rota nova). Sem isso, `/ping` e o painel continuam reportando a versão antiga, dando a falsa impressão de que um PDV ainda não recebeu um recurso/fix que já está rodando nele.
