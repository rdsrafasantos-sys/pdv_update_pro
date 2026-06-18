import logging
import os
import sys

from pdv_agent.config import LOG_FILE, PASTA_AGENTE


def configure_logging():
    os.makedirs(PASTA_AGENTE, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(
                open(sys.stdout.fileno(), mode="w", encoding="utf-8",
                     errors="replace", closefd=False)
            )
        ]
    )
    return logging.getLogger("pdv_agent")
