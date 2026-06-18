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
  installer/             script de instalação systemd para Ubuntu
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

Em produção, defina `PDV_AGENT_TOKEN`/`PDV_SERVER_TOKEN` com um valor forte e igual nos dois lados (no agente isso normalmente é feito configurando a variável de ambiente do serviço NSSM; no servidor, via `Environment=` no unit do systemd — ver `server/installer/instalar_servidor.sh`).

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

## Server — instalação (Ubuntu)

```bash
git clone <repo>
cd pdv-updater
./server/installer/instalar_servidor.sh
```

O script cria um virtualenv em `/opt/pdv-server/venv`, instala `server/requirements.txt` e registra o serviço `pdv-server` no systemd.

Para desenvolvimento local sem instalar como serviço:

```bash
cd server
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Versionamento

A versão do agente fica em `agent/src/pdv_agent/__init__.py` (`VERSION`) e é exposta em `/ping`. Ao lançar uma nova versão, atualize também `!define VERSAO` em `agent/installer/PDVAgent_Setup.nsi` para manter o instalador consistente.
