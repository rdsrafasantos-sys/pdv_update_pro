import datetime
import os

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Table, Text,
    UniqueConstraint, create_engine,
)
from sqlalchemy.orm import declarative_base, relationship, scoped_session, sessionmaker

from pdv_server.config import AUTH_DATA_DIR

Base = declarative_base()

# Associacao usuario <-> rede (permissao granular, fora do escopo de
# unidade inteira). Usada quando o usuario nao tem acesso_total_unidade.
usuario_redes = Table(
    "usuario_redes", Base.metadata,
    Column("usuario_id", Integer, ForeignKey("usuarios.id"), primary_key=True),
    Column("rede_id", Integer, ForeignKey("redes.id"), primary_key=True),
)

# Associacao usuario <-> unidade (acesso a TODAS as redes daquela unidade).
usuario_unidades = Table(
    "usuario_unidades", Base.metadata,
    Column("usuario_id", Integer, ForeignKey("usuarios.id"), primary_key=True),
    Column("unidade_id", Integer, ForeignKey("unidades.id"), primary_key=True),
)


class Unidade(Base):
    __tablename__ = "unidades"

    id = Column(Integer, primary_key=True)
    nome = Column(String(120), nullable=False, unique=True)
    criado_em = Column(DateTime, default=datetime.datetime.utcnow)

    redes = relationship("Rede", back_populates="unidade")


class Rede(Base):
    __tablename__ = "redes"
    __table_args__ = (UniqueConstraint("nome_fantasia", name="uq_rede_nome_fantasia"),)

    id = Column(Integer, primary_key=True)
    nome_fantasia = Column(String(120), nullable=False)
    razao_social = Column(String(200), nullable=True)
    # CNPJ identifica a empresa de forma unica. Unicidade validada em gestao.py,
    # nao aqui -- nullable porque redes criadas antes desta coluna nao tem o valor.
    cnpj = Column(String(14), nullable=True)
    unidade_id = Column(Integer, ForeignKey("unidades.id"), nullable=False)

    # Segredos sempre cifrados em repouso (ver auth/crypto.py) -- nunca
    # gravar texto puro nessas colunas.
    mongo_uri_cifrado = Column(Text, nullable=False)
    token_cifrado = Column(Text, nullable=False)
    tailscale_site_id_cifrado = Column(Text, nullable=True)

    ativa = Column(Boolean, default=True)
    criado_em = Column(DateTime, default=datetime.datetime.utcnow)

    unidade = relationship("Unidade", back_populates="redes")


class Perfil(Base):
    __tablename__ = "perfis"

    id = Column(Integer, primary_key=True)
    nome = Column(String(80), nullable=False, unique=True)
    descricao = Column(String(255), nullable=True)

    # Capacidades do perfil -- escopo (quais unidades/redes) fica no usuario,
    # nao no perfil (ver Usuario.unidades/redes/acesso_total).
    pode_gerenciar_redes = Column(Boolean, default=False)
    pode_gerenciar_usuarios = Column(Boolean, default=False)
    somente_leitura = Column(Boolean, default=False)
    pode_ver_fiscal = Column(Boolean, default=False)
    # Atualizações — granular por aba / ação
    pode_atu_agente = Column(Boolean, default=False)
    pode_atu_pdv_upload = Column(Boolean, default=False)
    pode_atu_pdv_disparar = Column(Boolean, default=False)
    pode_atu_pdv_limpar = Column(Boolean, default=False)
    pode_atu_integrador = Column(Boolean, default=False)
    # Replicações
    pode_replic_verificar = Column(Boolean, default=False)
    pode_replic_config = Column(Boolean, default=False)
    # Configurações
    pode_config_banco = Column(Boolean, default=False)
    pode_config_integrador = Column(Boolean, default=False)

    criado_em = Column(DateTime, default=datetime.datetime.utcnow)

    usuarios = relationship("Usuario", back_populates="perfil")


class Usuario(Base):
    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True)
    nome = Column(String(120), nullable=False)
    email = Column(String(160), nullable=False, unique=True)
    senha_hash = Column(String(255), nullable=False)

    perfil_id = Column(Integer, ForeignKey("perfis.id"), nullable=True)
    is_super_admin = Column(Boolean, default=False)
    acesso_total = Column(Boolean, default=False)  # ve todas unidades/redes

    ativo = Column(Boolean, default=True)

    totp_secret_cifrado = Column(Text, nullable=True)
    totp_habilitado = Column(Boolean, default=False)

    criado_em = Column(DateTime, default=datetime.datetime.utcnow)
    ultimo_login_em = Column(DateTime, nullable=True)

    perfil = relationship("Perfil", back_populates="usuarios")
    unidades = relationship("Unidade", secondary=usuario_unidades)
    redes = relationship("Rede", secondary=usuario_redes)


class InstalacaoSiteId(Base):
    """Registro de cada Tailscale Site ID alocado pela tela de Instalacao
    (painel/routes.py) -- garante que nenhum numero seja gerado duas vezes,
    mesmo que a Rede correspondente nunca chegue a ser criada ou seja
    excluida depois."""
    __tablename__ = "instalacao_site_ids"

    id = Column(Integer, primary_key=True)
    site_id = Column(Integer, nullable=False, unique=True)
    cliente_nome = Column(String(120), nullable=True)
    cliente_cnpj = Column(String(14), nullable=True)
    usuario_email = Column(String(160), nullable=True)
    criado_em = Column(DateTime, default=datetime.datetime.utcnow)

    # Progresso da automacao do service manager (ponto 2/3 do onboarding).
    # status: site_id_gerado -> script_gerado -> conectado -> concluido | erro
    status = Column(String(30), default="site_id_gerado")
    erp_ip = Column(String(45), nullable=True)
    token_callback = Column(String(64), nullable=True, unique=True)
    tailscale_hostname = Column(String(120), nullable=True)
    tailscale_ip = Column(String(45), nullable=True)
    faixas_detectadas = Column(Text, nullable=True)  # CSV de CIDRs (ex: 192.168.1.0/24,...)
    prefixos_ipv6 = Column(Text, nullable=True)      # CSV de prefixos 4via6 anunciados
    erro_mensagem = Column(Text, nullable=True)
    atualizado_em = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    # ID da Rede criada automaticamente no passo 3 do wizard de instalacao.
    rede_id = Column(Integer, ForeignKey("redes.id"), nullable=True)


class ChavePool(Base):
    """Pool de auth keys pre-geradas via OAuth para uso na tela de Instalacao.
    Chaves sao geradas em background, guardadas cifradas e consumidas uma a uma
    (reusable=False) -- sem nunca bloquear a geracao do script no click do admin."""
    __tablename__ = "chave_pool"

    id = Column(Integer, primary_key=True)
    chave_cifrada = Column(Text, nullable=False)
    descricao = Column(String(120), nullable=True)
    criado_em = Column(DateTime, default=datetime.datetime.utcnow)
    expira_em = Column(DateTime, nullable=True)
    usada = Column(Boolean, default=False)


class Auditoria(Base):
    __tablename__ = "auditoria"

    id = Column(Integer, primary_key=True)
    usuario_email = Column(String(160), nullable=True)
    acao = Column(String(80), nullable=False)
    detalhes = Column(Text, nullable=True)
    ip = Column(String(64), nullable=True)
    criado_em = Column(DateTime, default=datetime.datetime.utcnow)


os.makedirs(AUTH_DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(AUTH_DATA_DIR, "painel.db")
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = scoped_session(sessionmaker(bind=engine))

# Fabrica de sessoes independentes (nao thread-local) para uso em background
# threads e em funcoes chamadas de dentro de outras que ja tem SessionLocal aberta
# -- evita conflito de sessao compartilhada via scoped_session.
def nova_sessao():
    return sessionmaker(bind=engine)()


def init_db():
    Base.metadata.create_all(engine)
    _migrar_colunas_novas()


def _migrar_colunas_novas():
    """create_all() so cria TABELAS que faltam, nao COLUNAS novas em tabelas
    que ja existem -- instalacoes anteriores a Fase 1 ja tem "perfis" sem as
    colunas de permissao. Adiciona com ALTER TABLE, idempotente."""
    import sqlalchemy as sa

    colunas_novas = {
        "perfis": {
            "pode_gerenciar_redes": "BOOLEAN DEFAULT 0",
            "pode_gerenciar_usuarios": "BOOLEAN DEFAULT 0",
            "somente_leitura": "BOOLEAN DEFAULT 0",
            "pode_ver_fiscal": "BOOLEAN DEFAULT 0",
            "pode_atu_agente": "BOOLEAN DEFAULT 0",
            "pode_atu_pdv_upload": "BOOLEAN DEFAULT 0",
            "pode_atu_pdv_disparar": "BOOLEAN DEFAULT 0",
            "pode_atu_pdv_limpar": "BOOLEAN DEFAULT 0",
            "pode_atu_integrador": "BOOLEAN DEFAULT 0",
            "pode_replic_verificar": "BOOLEAN DEFAULT 0",
            "pode_replic_config": "BOOLEAN DEFAULT 0",
            "pode_config_banco": "BOOLEAN DEFAULT 0",
            "pode_config_integrador": "BOOLEAN DEFAULT 0",
        },
        "redes": {
            "cnpj": "VARCHAR(14)",
            "razao_social": "VARCHAR(200)",
        },
        "instalacao_site_ids": {
            "rede_id": "INTEGER",
        },
    }
    with engine.connect() as conn:
        for tabela, colunas in colunas_novas.items():
            existentes = {row[1] for row in conn.execute(sa.text(f"PRAGMA table_info({tabela})"))}
            for col, tipo in colunas.items():
                if col not in existentes:
                    conn.execute(sa.text(f"ALTER TABLE {tabela} ADD COLUMN {col} {tipo}"))
        # Renomeia nome -> nome_fantasia nas instalacoes existentes (SQLite 3.25+)
        redes_cols = {row[1] for row in conn.execute(sa.text("PRAGMA table_info(redes)"))}
        if "nome" in redes_cols and "nome_fantasia" not in redes_cols:
            conn.execute(sa.text("ALTER TABLE redes RENAME COLUMN nome TO nome_fantasia"))
        conn.commit()
