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
    __table_args__ = (UniqueConstraint("nome", name="uq_rede_nome"),)

    id = Column(Integer, primary_key=True)
    nome = Column(String(120), nullable=False)
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


def init_db():
    Base.metadata.create_all(engine)
