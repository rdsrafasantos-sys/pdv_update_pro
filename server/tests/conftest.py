"""Configuracao compartilhada dos testes -- garante que os modulos do
pdv_server importam sem tocar em nenhum ambiente real. config.py exige e
valida essas variaveis na importacao (ver server/src/pdv_server/config.py),
entao precisam estar definidas ANTES de qualquer teste importar pdv_server.*.
"""
import os

os.environ.setdefault("PDV_SERVER_TOKEN", "x" * 32)
os.environ.setdefault("PDV_SECRET_KEY", "y" * 32)
os.environ.setdefault("PDV_SERVER_MONGO_URI", "mongodb://localhost:27016")

if "PDV_MASTER_KEY" not in os.environ:
    from cryptography.fernet import Fernet
    os.environ["PDV_MASTER_KEY"] = Fernet.generate_key().decode()
