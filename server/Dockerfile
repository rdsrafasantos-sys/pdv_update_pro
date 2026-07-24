FROM python:3.12-slim

WORKDIR /opt/pdv-server

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# gosu para troca segura de usuário no entrypoint + usuário não-root para a aplicação
RUN apt-get update && apt-get install -y --no-install-recommends gosu curl && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd -r pdv -g 999 && useradd -r -u 999 -g pdv -m pdv

# CLI do Tailscale -- necessario para tailscale_api.prefixo_esperado() (auth/gestao_instalacao.py),
# que recalcula localmente o prefixo 4via6 esperado via "tailscale debug via" antes de aplicar
# qualquer coisa no ACL automaticamente. Sem o binario aqui dentro, essa checagem de seguranca
# sempre falha e a instalacao cai no fallback manual (concluido_pendente_acl) mesmo quando tudo
# deu certo. So precisa do binario/CLI -- este container nunca roda "tailscale up" nele mesmo
# (quem entra na tailnet e o HOST, via network_mode: host).
RUN curl -fsSL https://tailscale.com/install.sh | sh

COPY . .
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && chown -R pdv:pdv /opt/pdv-server

EXPOSE 8888

ENTRYPOINT ["/entrypoint.sh"]
CMD ["gunicorn", "--config", "gunicorn.conf.py", "wsgi:app"]
