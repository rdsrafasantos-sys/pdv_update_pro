"""Entrypoint do Gunicorn. Exporta `app` para o servidor WSGI."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from pdv_server.app import app  # noqa: F401 — exportado para o Gunicorn
