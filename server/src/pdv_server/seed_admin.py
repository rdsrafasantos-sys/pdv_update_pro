"""Cria o primeiro super-admin do painel. So funciona se nao houver
nenhum usuario cadastrado ainda (evita criar admins extras por engano).

Uso:
    python -m pdv_server.seed_admin [email] [senha] [nome]

Sem argumentos, pergunta interativamente.
"""
import getpass
import sys

from pdv_server.auth.models import SessionLocal, Usuario, init_db
from pdv_server.auth.security import gerar_hash_senha


def main():
    init_db()
    db = SessionLocal()
    try:
        if db.query(Usuario).count() > 0:
            print("Já existe pelo menos um usuário cadastrado. Abortando "
                  "(use a tela de Usuários para criar os demais).")
            sys.exit(1)

        email = sys.argv[1] if len(sys.argv) > 1 else input("E-mail: ").strip().lower()
        senha = sys.argv[2] if len(sys.argv) > 2 else getpass.getpass("Senha: ")
        nome = sys.argv[3] if len(sys.argv) > 3 else input("Nome: ").strip()

        if not email or not senha or not nome:
            print("E-mail, senha e nome são obrigatórios.")
            sys.exit(1)

        usuario = Usuario(
            nome=nome, email=email, senha_hash=gerar_hash_senha(senha),
            is_super_admin=True, acesso_total=True, ativo=True,
        )
        db.add(usuario)
        db.commit()
        print(f"Super-admin criado: {email}")
        print("Acesse o painel e configure o 2FA no primeiro login.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
