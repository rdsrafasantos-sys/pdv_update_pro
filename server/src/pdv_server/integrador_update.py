"""
Atualização do VR Integrador via SSH.

Fluxo:
  1. SSH no host do integrador
  2. Lê ~/.vr/docker-compose-integrador.yml
  3. Substitui a tag de versão da imagem vrsoftbr/vrintegradormaster
  4. Stop/up dos containers
  5. Yield de linhas de log para streaming SSE
"""
import re
import time

COMPOSE_PATH = ".vr/docker-compose-integrador.yml"
IMAGE_PREFIX = "vrsoftbr/vrintegradormaster:"
CONTAINERS = ["vrintegradormaster", "vrintegrador-mongo"]
COMPOSE_CMD = f"docker compose -f {COMPOSE_PATH}"


def _ssh(cfg: dict):
    import paramiko
    client = paramiko.SSHClient()
    # WarningPolicy: loga host desconhecido mas não bloqueia. Melhor que AutoAddPolicy
    # (que aceita silenciosamente) enquanto ainda permite conectar sem known_hosts.
    client.set_missing_host_key_policy(paramiko.WarningPolicy())
    client.connect(
        hostname=cfg["ssh_ip"],
        port=int(cfg.get("ssh_porta") or 22),
        username=cfg["ssh_usuario"],
        password=cfg.get("ssh_senha") or None,
        timeout=10,
    )
    return client


def _exec(client, cmd: str) -> tuple[str, str, int]:
    _, stdout, stderr = client.exec_command(cmd)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    code = stdout.channel.recv_exit_status()
    return out, err, code


def versao_atual(cfg: dict) -> dict:
    """Lê a versão atual da imagem no docker-compose do integrador."""
    try:
        client = _ssh(cfg)
        out, err, code = _exec(client, f"cat ~/{COMPOSE_PATH}")
        client.close()
        if code != 0:
            return {"erro": err.strip() or "Arquivo não encontrado", "versao": None}
        m = re.search(rf"{re.escape(IMAGE_PREFIX)}([^\s\"']+)", out)
        if not m:
            return {"erro": "Tag de versão não encontrada no docker-compose", "versao": None}
        return {"erro": None, "versao": m.group(1), "compose": out}
    except Exception as e:
        return {"erro": str(e), "versao": None}


def atualizar_stream(cfg: dict, nova_versao: str):
    """
    Generator — yield de dicts {"tipo": "passo"|"log"|"erro"|"fim", "texto": str}.
    Cada dict é serializado como linha SSE pelo caller.
    """
    def passo(txt):
        return {"tipo": "passo", "texto": txt}

    def log(txt):
        return {"tipo": "log", "texto": txt}

    def erro(txt):
        return {"tipo": "erro", "texto": txt}

    # Valida formato da versão
    if not re.match(r'^v?\d+\.\d+\.\d+', nova_versao):
        yield erro(f"Versão inválida: '{nova_versao}'. Use o formato v2.3.0")
        yield {"tipo": "fim", "sucesso": False}
        return

    try:
        # ── 1. Conectar ───────────────────────────────────────────
        yield passo("Conectando via SSH...")
        import paramiko
        client = _ssh(cfg)
        yield passo(f"✔ Conectado em {cfg['ssh_ip']}")

        # ── 2. Ler compose atual ──────────────────────────────────
        yield passo("Lendo docker-compose-integrador.yml...")
        out, err, code = _exec(client, f"cat ~/{COMPOSE_PATH}")
        if code != 0:
            yield erro(f"Erro ao ler compose: {err.strip()}")
            client.close()
            yield {"tipo": "fim", "sucesso": False}
            return

        m = re.search(rf"{re.escape(IMAGE_PREFIX)}([^\s\"']+)", out)
        if not m:
            yield erro("Tag de versão não encontrada no arquivo compose.")
            client.close()
            yield {"tipo": "fim", "sucesso": False}
            return

        versao_antiga = m.group(1)
        yield passo(f"✔ Versão atual: {versao_antiga}")

        if versao_antiga == nova_versao:
            yield passo(f"ℹ Integrador já está na versão {nova_versao}. Nenhuma alteração necessária.")
            client.close()
            yield {"tipo": "fim", "sucesso": True}
            return

        # ── 3. Atualizar versão no arquivo ────────────────────────
        yield passo(f"Atualizando versão {versao_antiga} → {nova_versao}...")
        novo_conteudo = out.replace(
            f"{IMAGE_PREFIX}{versao_antiga}",
            f"{IMAGE_PREFIX}{nova_versao}",
        )
        # Escreve via heredoc para evitar problemas com caracteres especiais
        escaped = novo_conteudo.replace("'", "'\\''")
        _, err_w, code_w = _exec(client, f"cat > ~/{COMPOSE_PATH} << 'HEREDOC_EOF'\n{novo_conteudo}\nHEREDOC_EOF")
        if code_w != 0:
            yield erro(f"Erro ao gravar compose: {err_w.strip()}")
            client.close()
            yield {"tipo": "fim", "sucesso": False}
            return
        yield passo(f"✔ Arquivo atualizado com sucesso")

        # ── 4. Parar containers ───────────────────────────────────
        yield passo("Parando containers...")
        for container in CONTAINERS:
            yield log(f"  → stop {container}")
            out_s, err_s, _ = _exec(client, f"cd ~ && {COMPOSE_CMD} stop {container} 2>&1")
            for linha in (out_s + err_s).splitlines():
                if linha.strip():
                    yield log(f"    {linha}")
        yield passo("✔ Containers parados")

        # ── 5. Subir containers ───────────────────────────────────
        yield passo("Iniciando containers com nova versão...")
        for container in CONTAINERS:
            yield log(f"  → up -d {container}")
            out_u, err_u, _ = _exec(client, f"cd ~ && {COMPOSE_CMD} up -d {container} 2>&1")
            for linha in (out_u + err_u).splitlines():
                if linha.strip():
                    yield log(f"    {linha}")
        yield passo("✔ Containers iniciados")

        # ── 6. Aguardar inicialização ─────────────────────────────
        yield passo("Aguardando inicialização (10s)...")
        time.sleep(10)

        # ── 7. Verificar status ───────────────────────────────────
        yield passo("Verificando status dos containers...")
        out_ps, _, _ = _exec(client, f"cd ~ && {COMPOSE_CMD} ps 2>&1")
        for linha in out_ps.splitlines():
            if linha.strip():
                yield log(f"  {linha}")

        # ── 8. Logs recentes ──────────────────────────────────────
        yield passo("Coletando logs do vrintegradormaster...")
        out_log, _, _ = _exec(client, f"cd ~ && {COMPOSE_CMD} logs --tail=50 vrintegradormaster 2>&1")
        for linha in out_log.splitlines():
            if linha.strip():
                yield log(linha)

        client.close()
        yield passo(f"✅ Atualização para {nova_versao} concluída!")
        yield {"tipo": "fim", "sucesso": True, "versao": nova_versao}

    except Exception as e:
        yield erro(f"Erro inesperado: {e}")
        yield {"tipo": "fim", "sucesso": False}


def config_ssh_completa(cfg: dict) -> bool:
    """Retorna True se os campos SSH obrigatórios estão presentes."""
    return bool(cfg.get("ssh_ip") and cfg.get("ssh_usuario"))
