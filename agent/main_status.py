"""Entrypoint do monitor de status (status_pdv.exe). Roda na sessao do usuario."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from pdv_agent.status_app import main

if __name__ == "__main__":
    main()
