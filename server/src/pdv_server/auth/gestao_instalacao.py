"""Automacao do wizard de instalacao de clientes novos via Tailscale (alocacao
de Site ID, geracao do script do service manager, callback de progresso,
aprovacao de rotas/ACL) -- extraido de auth/gestao.py (Fase 4, divisao por
dominio: gestao de usuario/rede vs automacao do wizard de instalacao sao
dois assuntos bem diferentes que só coincidiam por mexerem em SQLAlchemy).

Depende de gestao.py (core) para validar_cnpj/_checar_cnpj_disponivel
(unicidade de CNPJ) e criar_rede (a Rede final e criada com a logica de
sempre). Quem usa: painel/routes.py e main.py (pre-aquecimento do pool)."""
import datetime
import logging
import secrets
import threading

from pdv_server import tailscale_api
from pdv_server.auth.crypto import cifrar, decifrar
from pdv_server.auth.gestao import (
    _checar_cnpj_disponivel, carregar_permissoes, criar_rede, validar_cnpj,
)
from pdv_server.auth.models import (
    ChavePool, InstalacaoSiteId, Rede, SessionLocal, Usuario, nova_sessao,
)
from pdv_server.config import PAINEL_CALLBACK_URL, TAILSCALE_AUTH_KEY_SERVICE_MANAGER

log = logging.getLogger(__name__)

# ── Pool de auth keys pre-geradas ────────────────────────────────────────────
# Chaves geradas via OAuth em background, cifradas no banco. Geração de script
# pega do pool (instantâneo, zero chamada de rede). Pool reposto automaticamente
# após cada uso e na inicialização do servidor.

_POOL_ALVO = 3      # numero de chaves que o pool deve manter disponiveis
_POOL_EXPIRY_DIAS = 25  # cada chave expira em 25 dias (bem antes dos 90 do Tailscale)


def _chaves_disponiveis_count(db):
    agora = datetime.datetime.utcnow()
    return db.query(ChavePool).filter(
        ChavePool.usada == False,
        (ChavePool.expira_em == None) | (ChavePool.expira_em > agora),
    ).count()


def obter_chave_do_pool():
    """Retira a chave mais antiga disponivel do pool e marca como usada.
    Usa sessao independente (nova_sessao) para nao interferir com sessoes
    scoped_session da funcao chamadora. Dispara reposicao em background."""
    db = nova_sessao()
    try:
        agora = datetime.datetime.utcnow()
        chave = (
            db.query(ChavePool)
            .filter(
                ChavePool.usada == False,
                (ChavePool.expira_em == None) | (ChavePool.expira_em > agora),
            )
            .order_by(ChavePool.criado_em.asc())
            .first()
        )
        if not chave:
            return None
        chave.usada = True
        db.commit()
        valor = decifrar(chave.chave_cifrada)
        threading.Thread(target=repor_pool_background, daemon=True).start()
        return valor
    finally:
        db.close()


def _gerar_e_salvar_chave_no_pool():
    """Chama a API do Tailscale e persiste a nova chave cifrada no pool."""
    chave_valor = tailscale_api.criar_auth_key(
        tags=["tag:pdv-service-manager"],
        descricao="pool-instalacao-auto",
        expiry_seconds=_POOL_EXPIRY_DIAS * 24 * 3600,
    )
    expira_em = datetime.datetime.utcnow() + datetime.timedelta(days=_POOL_EXPIRY_DIAS)
    db = nova_sessao()
    try:
        db.add(ChavePool(chave_cifrada=cifrar(chave_valor), expira_em=expira_em))
        db.commit()
    finally:
        db.close()


def repor_pool_background():
    """Daemon thread: verifica quantas chaves disponiveis ha e gera novas ate
    atingir _POOL_ALVO. Silencioso se OAuth nao estiver configurado."""
    if not tailscale_api.automacao_disponivel():
        return
    try:
        db = nova_sessao()
        try:
            faltam = _POOL_ALVO - _chaves_disponiveis_count(db)
        finally:
            db.close()
        for _ in range(max(0, faltam)):
            _gerar_e_salvar_chave_no_pool()
    except Exception:
        pass


def status_pool():
    """Para uso administrativo -- retorna contagem atual do pool."""
    db = nova_sessao()
    try:
        agora = datetime.datetime.utcnow()
        total = db.query(ChavePool).count()
        disponiveis = _chaves_disponiveis_count(db)
        usadas = db.query(ChavePool).filter(ChavePool.usada == True).count()
        expiradas = db.query(ChavePool).filter(
            ChavePool.usada == False,
            ChavePool.expira_em != None,
            ChavePool.expira_em <= agora,
        ).count()
        return {
            "disponiveis": disponiveis,
            "usadas": usadas,
            "expiradas": expiradas,
            "total": total,
            "alvo": _POOL_ALVO,
            "automacao_disponivel": tailscale_api.automacao_disponivel(),
        }
    finally:
        db.close()


# ── Instalacao (alocacao de Tailscale Site ID) ──────────────────
# Site ID precisa ser unico pra sempre na tailnet -- mesmo que a rede
# correspondente nunca seja criada, ou seja excluida depois, o numero
# alocado aqui nunca pode ser reaproveitado. Por isso o "maior valor" e
# calculado tanto a partir das Redes existentes (campo pode ter sido
# preenchido a mao, como o Site ID 7 da TEST) quanto do historico desta
# tabela, e o registro e gravado ANTES de a rede existir.

def _maior_site_id_existente(db):
    maior_redes = 0
    for rede in db.query(Rede).all():
        if rede.tailscale_site_id_cifrado:
            valor = decifrar(rede.tailscale_site_id_cifrado)
            if valor and valor.strip().isdigit():
                maior_redes = max(maior_redes, int(valor))
    maior_alocados = max(
        (r.site_id for r in db.query(InstalacaoSiteId).all()), default=0
    )
    return max(maior_redes, maior_alocados)


def listar_site_ids_instalacao():
    db = SessionLocal()
    try:
        registros = db.query(InstalacaoSiteId).order_by(InstalacaoSiteId.site_id.desc()).all()
        return [_instalacao_para_dict(r) for r in registros]
    finally:
        db.close()


def gerar_proximo_site_id(cliente_cnpj, cliente_nome="", usuario_email=""):
    cnpj_normalizado = validar_cnpj(cliente_cnpj)
    db = SessionLocal()
    try:
        _checar_cnpj_disponivel(db, cnpj_normalizado)
        if db.query(InstalacaoSiteId).filter_by(cliente_cnpj=cnpj_normalizado).first():
            raise ValueError("Ja existe um Site ID gerado para este CNPJ -- veja o historico abaixo.")

        proximo = _maior_site_id_existente(db) + 1
        registro = InstalacaoSiteId(
            site_id=proximo,
            cliente_nome=(cliente_nome or "").strip() or None,
            cliente_cnpj=cnpj_normalizado,
            usuario_email=(usuario_email or "").strip() or None,
        )
        db.add(registro)
        db.commit()
        return {
            "id": registro.id,
            "site_id": registro.site_id,
            "cliente_nome": registro.cliente_nome,
            "cliente_cnpj": registro.cliente_cnpj,
            "usuario_email": registro.usuario_email,
            "criado_em": registro.criado_em.strftime("%Y-%m-%d %H:%M"),
        }
    finally:
        db.close()


def _instalacao_para_dict(registro):
    # Mongo URI sugerida: derivada do IP Tailscale conhecido apos conexao
    mongo_uri_sugerida = (
        f"mongodb://{registro.tailscale_ip}:27016"
        if registro.tailscale_ip else ""
    )
    return {
        "id": registro.id,
        "site_id": registro.site_id,
        "cliente_nome": registro.cliente_nome,
        "cliente_cnpj": registro.cliente_cnpj,
        "usuario_email": registro.usuario_email,
        "status": registro.status,
        "erp_ip": registro.erp_ip,
        "tailscale_hostname": registro.tailscale_hostname,
        "tailscale_ip": registro.tailscale_ip,
        "faixas_detectadas": registro.faixas_detectadas,
        "prefixos_ipv6": registro.prefixos_ipv6,
        "erro_mensagem": registro.erro_mensagem,
        "rede_id": registro.rede_id,
        "mongo_uri_sugerida": mongo_uri_sugerida,
        "criado_em": registro.criado_em.strftime("%Y-%m-%d %H:%M") if registro.criado_em else None,
        "atualizado_em": registro.atualizado_em.strftime("%Y-%m-%d %H:%M") if registro.atualizado_em else None,
    }


def obter_instalacao(instalacao_id):
    db = SessionLocal()
    try:
        registro = db.get(InstalacaoSiteId, instalacao_id)
        if not registro:
            raise ValueError("Instalacao nao encontrada.")
        return _instalacao_para_dict(registro)
    finally:
        db.close()


def gerar_script_instalacao(instalacao_id, erp_ip=""):
    """Gera (ou regenera) o script do service manager para esta instalacao.
    Exige a credencial da API do Tailscale configurada -- sem ela nao tem
    como mintar uma auth key de uso unico para o script."""
    from pdv_server.instalacao_script import gerar_script

    if not PAINEL_CALLBACK_URL:
        raise ValueError(
            "PDV_PAINEL_CALLBACK_URL nao configurada -- o script nao "
            "saberia para onde reportar o progresso."
        )

    db = SessionLocal()
    try:
        registro = db.get(InstalacaoSiteId, instalacao_id)
        if not registro:
            raise ValueError("Instalacao nao encontrada.")

        registro.erp_ip = (erp_ip or "").strip() or None
        if not registro.token_callback:
            registro.token_callback = secrets.token_urlsafe(32)

        # Prioridade: pool (instantaneo, pre-gerado) > env var (estatico) > API (lento)
        auth_key = obter_chave_do_pool()
        if not auth_key:
            if TAILSCALE_AUTH_KEY_SERVICE_MANAGER:
                auth_key = TAILSCALE_AUTH_KEY_SERVICE_MANAGER
            elif tailscale_api.automacao_disponivel():
                auth_key = tailscale_api.criar_auth_key(
                    tags=["tag:pdv-service-manager"],
                    descricao=f"instalacao-site-id-{registro.site_id}",
                )
            else:
                raise ValueError(
                    "Nenhuma auth key disponivel. Configure "
                    "PDV_TAILSCALE_AUTH_KEY_SERVICE_MANAGER no .env ou "
                    "PDV_TAILSCALE_OAUTH_CLIENT_ID/SECRET para geracao automatica."
                )

        callback_url = f"{PAINEL_CALLBACK_URL.rstrip('/')}/api/instalacao/callback/{registro.token_callback}"
        script = gerar_script(registro.site_id, auth_key, registro.erp_ip, callback_url)

        registro.status = "script_gerado"
        registro.erro_mensagem = None
        db.commit()
        return script
    finally:
        db.close()


def processar_callback_instalacao(token_callback, payload):
    """Recebe o status reportado pelo script rodando no service manager.
    Quando o status e 'concluido', tenta automatizar o resto (aprovar
    rotas + acrescentar os prefixos no ACL) -- se a automacao falhar (ou
    nao estiver configurada), a instalacao fica marcada com o erro pra
    resolver manualmente, mas o que o script ja fez (Tailscale conectado,
    rotas anunciadas) nao se perde."""
    db = SessionLocal()
    try:
        registro = db.query(InstalacaoSiteId).filter_by(token_callback=token_callback).first()
        if not registro:
            raise ValueError("Token de callback invalido.")

        status = payload.get("status", "")
        if status == "erro":
            registro.status = "erro"
            registro.erro_mensagem = payload.get("mensagem", "Erro desconhecido no script.")
        elif status == "conectado":
            registro.status = "conectado"
            registro.tailscale_ip = payload.get("tailscale_ip")
            registro.tailscale_hostname = payload.get("tailscale_hostname")
        elif status == "concluido":
            registro.tailscale_ip = payload.get("tailscale_ip") or registro.tailscale_ip
            registro.tailscale_hostname = payload.get("tailscale_hostname") or registro.tailscale_hostname
            registro.faixas_detectadas = payload.get("faixas")
            registro.prefixos_ipv6 = payload.get("prefixos")
            registro.status = "concluido"
            db.commit()
            # Automacao (aprovacao de rotas + ACL) roda em background para
            # nao bloquear o callback -- o script recebe a resposta
            # imediatamente, o browser verifica o status final via polling.
            instalacao_id = registro.id
            threading.Thread(
                target=_finalizar_automacao_em_background,
                args=(instalacao_id,),
                daemon=True,
            ).start()
            return _instalacao_para_dict(registro)
        else:
            registro.status = "iniciando"

        db.commit()
        return _instalacao_para_dict(registro)
    finally:
        db.close()


def criar_rede_da_instalacao(instalacao_id, token, unidade_id, mongo_uri=None):
    """Cria a Rede usando dados ja coletados pela tela de Instalacao (nome,
    CNPJ, site_id, tailscale_ip) e vincula o registro de instalacao a ela.
    Usa nova_sessao() em tres fases para evitar DetachedInstanceError causado
    pelo conflito entre a sessao desta funcao e a da criar_rede() chamada internamente."""
    token = (token or "").strip()
    if not token:
        raise ValueError("Token do agente e obrigatorio.")

    # Fase 1: leitura -- extrai tudo necessario de registro antes de chamar criar_rede()
    db = nova_sessao()
    try:
        registro = db.get(InstalacaoSiteId, instalacao_id)
        if not registro:
            raise ValueError("Instalacao nao encontrada.")
        if registro.rede_id:
            raise ValueError("Uma Rede ja foi criada para esta instalacao.")
        if not registro.tailscale_ip:
            raise ValueError("O service manager ainda nao se conectou -- aguarde o status 'Concluido'.")
        tailscale_ip = registro.tailscale_ip
        cliente_nome = registro.cliente_nome
        cliente_cnpj = registro.cliente_cnpj
        site_id = registro.site_id
    finally:
        db.close()

    mongo_uri_final = (mongo_uri or "").strip() or f"mongodb://{tailscale_ip}:27016"

    # Fase 2: criacao da Rede (usa SessionLocal propria internamente, sem conflito)
    rede = criar_rede(
        nome_fantasia=cliente_nome or f"Rede {site_id}",
        unidade_id=unidade_id,
        mongo_uri=mongo_uri_final,
        token=token,
        tailscale_site_id=str(site_id),
        cnpj=cliente_cnpj or "",
    )

    # Fase 3: vincula a Rede ao registro de instalacao e retorna o dict atualizado
    db2 = nova_sessao()
    try:
        registro2 = db2.get(InstalacaoSiteId, instalacao_id)
        registro2.rede_id = rede["id"]
        db2.commit()
        resultado_dict = _instalacao_para_dict(registro2)  # lido enquanto sessao ainda aberta
    finally:
        db2.close()

    return {"rede": rede, "instalacao": resultado_dict}


def _finalizar_automacao_em_background(instalacao_id):
    """Roda em daemon thread -- abre a propria sessao de DB, atualiza o
    status final apos aprovacao de rotas e ACL via Tailscale API."""
    db = SessionLocal()
    try:
        registro = db.get(InstalacaoSiteId, instalacao_id)
        if not registro:
            return
        _finalizar_automacao_instalacao(registro)
        db.commit()
    except Exception:
        log.exception("Falha ao finalizar automacao da instalacao %s", instalacao_id)
    finally:
        db.close()


def _finalizar_automacao_instalacao(registro):
    """Best-effort: aprova as rotas anunciadas e acrescenta os prefixos no
    ACL via API do Tailscale. So roda se a credencial estiver configurada
    -- senao, deixa status 'concluido_manual' com os prefixos visiveis pra
    quem administra colar manualmente no admin console."""
    faixas = [f for f in (registro.faixas_detectadas or "").split(",") if f]
    prefixos = [p for p in (registro.prefixos_ipv6 or "").split(",") if p]
    if not prefixos:
        registro.status = "erro"
        registro.erro_mensagem = "Script concluiu mas nao reportou nenhum prefixo IPv6."
        return

    # O callback que populou faixas/prefixos so e autenticado por um token
    # de uso unico (sem sessao de usuario) -- recalcula localmente o
    # prefixo esperado para o site_id desta instalacao e compara antes de
    # confiar no valor reportado para tocar no ACL. Qualquer divergencia
    # (ou impossibilidade de validar) cai no fallback manual em vez de
    # aplicar automaticamente algo que nao foi verificado.
    if len(faixas) != len(prefixos):
        registro.status = "concluido_pendente_acl"
        registro.erro_mensagem = (
            "Numero de faixas e prefixos reportados nao bate -- aprove a rota e "
            "acrescente os prefixos manualmente apos conferir."
        )
        return
    for faixa, prefixo_reportado in zip(faixas, prefixos):
        esperado = tailscale_api.prefixo_esperado(registro.site_id, faixa)
        if esperado is not None and esperado.strip().lower() != prefixo_reportado.strip().lower():
            registro.status = "concluido_pendente_acl"
            registro.erro_mensagem = (
                f"O prefixo reportado para a faixa {faixa} nao corresponde ao "
                f"esperado para o site_id {registro.site_id} -- aplicação automática "
                f"do ACL bloqueada por segurança. Confira manualmente antes de "
                f"aprovar a rota."
            )
            return

    if not tailscale_api.automacao_disponivel():
        registro.status = "concluido_manual"
        return

    try:
        dispositivo = tailscale_api.obter_dispositivo_por_hostname(registro.tailscale_hostname)
        if not dispositivo:
            raise ValueError(f"Dispositivo '{registro.tailscale_hostname}' nao encontrado na tailnet ainda.")
        tailscale_api.aprovar_rotas(dispositivo["id"], prefixos)
        tailscale_api.adicionar_prefixos_ao_grant("tag:pdv-service-manager", prefixos)
        registro.status = "concluido"
        registro.erro_mensagem = None
    except Exception as e:
        registro.status = "concluido_pendente_acl"
        registro.erro_mensagem = (
            f"Service manager conectado e rotas anunciadas, mas a automacao do "
            f"ACL falhou: {e}. Aprove a rota e acrescente os prefixos manualmente."
        )


def instalacoes_visiveis_para(usuario_id):
    """Lista as instalacoes (site-ids) que esse usuario pode ver. Antes da
    Rede ser criada (Passo 3 do wizard) o registro nao tem unidade_id --
    entao quem nao tem acesso_total so ve as instalacoes que ele mesmo
    criou (usuario_email), para nao vazar CNPJ/IP/status de clientes de
    outra unidade/tecnico."""
    perm = carregar_permissoes(usuario_id)
    if not perm:
        return []
    todas = listar_site_ids_instalacao()
    if perm["acesso_total"]:
        return todas
    db = SessionLocal()
    try:
        usuario = db.get(Usuario, usuario_id)
        email = usuario.email if usuario else None
    finally:
        db.close()
    return [r for r in todas if email and r["usuario_email"] == email]


def usuario_pode_acessar_instalacao(usuario_id, instalacao_id):
    perm = carregar_permissoes(usuario_id)
    if not perm:
        return False
    if perm["acesso_total"]:
        return True
    db = SessionLocal()
    try:
        usuario = db.get(Usuario, usuario_id)
        registro = db.get(InstalacaoSiteId, instalacao_id)
        return bool(usuario and registro and registro.usuario_email == usuario.email)
    finally:
        db.close()
