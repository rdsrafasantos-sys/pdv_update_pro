FROM python:3.12-slim

WORKDIR /opt/pdv-server

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8888

CMD ["gunicorn", "--config", "gunicorn.conf.py", "wsgi:app"]
