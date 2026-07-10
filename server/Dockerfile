FROM python:3.12-slim

WORKDIR /opt/pdv-server

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# gosu para troca segura de usuário no entrypoint + usuário não-root para a aplicação
RUN apt-get update && apt-get install -y --no-install-recommends gosu && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd -r pdv -g 999 && useradd -r -u 999 -g pdv pdv

COPY . .
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && chown -R pdv:pdv /opt/pdv-server

EXPOSE 8888

ENTRYPOINT ["/entrypoint.sh"]
CMD ["gunicorn", "--config", "gunicorn.conf.py", "wsgi:app"]
