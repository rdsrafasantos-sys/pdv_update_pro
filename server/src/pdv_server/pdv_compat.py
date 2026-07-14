"""
Verificação de compatibilidade entre PDVPro e VRIntegradorMaster.

Busca e parseia a página de notas do PDVPro para extrair a versão mínima
do integrador exigida por cada versão. Cache local com TTL de 24h.
"""
import json
import os
import re
import time

import requests

from pdv_server.versioning import versao_para_tupla

URL_NOTAS_PDVPRO = (
    "https://docs.vrsoft.com.br/vrsuper/outros/vrpdvpro/notas-de-versao-vrpdvpro"
)
CACHE_TTL = 24 * 3600
_CACHE_NOME = "compat_cache.json"


def _cache_path(integrador_dir: str) -> str:
    return os.path.join(integrador_dir, _CACHE_NOME)


def _carregar_cache(integrador_dir: str) -> dict | None:
    try:
        path = _cache_path(integrador_dir)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if time.time() - data.get("ts", 0) > CACHE_TTL:
            return None
        return data.get("tabela") or None
    except Exception:
        return None


def _salvar_cache(integrador_dir: str, tabela: dict):
    try:
        with open(_cache_path(integrador_dir), "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "tabela": tabela}, f)
    except Exception:
        pass


def _parsear_tabela(html: str) -> dict:
    """Extrai {versao_pdvpro: versao_min_integrador} do HTML.

    GitBook renderiza versões como <p><strong>vX.X.X</strong> ... requisito ...</p>
    — sem usar <h2>/<h3>. Dividimos por tags de bloco e rastreamos a última
    versão PDVPro vista antes de cada requisito de integrador.
    """
    tabela = {}
    versao_atual = None

    # Divide por qualquer tag de bloco para processar cada elemento separadamente
    blocos = re.split(
        r"</?(?:p|div|li|ul|ol|section|article|h[1-6])[^>]*>",
        html,
        flags=re.IGNORECASE,
    )

    for bloco in blocos:
        texto = re.sub(r"<[^>]+>", " ", bloco).strip()
        if not texto:
            continue

        # Atualiza versão PDVPro se este bloco contém um número de versão
        m_pdv = re.search(r"\bv(\d+\.\d+\.\d+)\b", texto)
        if m_pdv:
            versao_atual = m_pdv.group(1)

        # Associa requisito do integrador à versão atual
        m_int = re.search(
            r"VRIntegradorMaster\s+v?(\d+\.\d+\.\d+)\s+ou\s+superior",
            texto, re.IGNORECASE,
        )
        if m_int and versao_atual:
            tabela[versao_atual] = m_int.group(1)

    return tabela


def buscar_tabela(integrador_dir: str, forcar: bool = False) -> dict:
    """Retorna a tabela de compatibilidade, usando cache quando válido.

    Em caso de falha de rede, cai de volta para o cache desatualizado.
    """
    if not forcar:
        cached = _carregar_cache(integrador_dir)
        if cached is not None:
            return cached
    try:
        r = requests.get(
            URL_NOTAS_PDVPRO,
            timeout=12,
            headers={"User-Agent": "PDVUpdater-Compat/1.0"},
        )
        r.raise_for_status()
        tabela = _parsear_tabela(r.text)
        if tabela:
            _salvar_cache(integrador_dir, tabela)
        return tabela
    except Exception:
        # fallback: cache desatualizado é melhor que nada
        try:
            path = _cache_path(integrador_dir)
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f).get("tabela") or {}
        except Exception:
            pass
        return {}


def versao_minima_para(tabela: dict, versao_pdvpro: str) -> str | None:
    """Retorna a versão mínima do integrador para uma versão do PDVPro.

    O requisito se propaga: se v7.0.0 exige integrador v3.0.0 e v8.3.0
    exige v3.3.2, então v8.1.0 (sem req. explícito) ainda exige v3.0.0.
    """
    if not tabela or not versao_pdvpro:
        return None
    try:
        alvo = versao_para_tupla(versao_pdvpro)
    except (ValueError, AttributeError):
        return None

    req = None
    for v in sorted(tabela.keys(), key=lambda x: versao_para_tupla(x)):
        try:
            if versao_para_tupla(v) <= alvo:
                req = tabela[v]
        except (ValueError, AttributeError):
            continue
    return req


def verificar(
    versao_pdvpro: str,
    versao_integrador: str | None,
    tabela: dict,
) -> dict:
    """Verifica se o integrador é compatível com a versão alvo do PDVPro.

    Retorna:
        ok          — True se compatível ou sem dados para bloquear
        bloqueado   — True apenas quando temos certeza de incompatibilidade
        versao_min  — versão mínima exigida, se houver
        versao_atual — versão do integrador informada
        aviso       — mensagem descritiva para exibir ao operador
    """
    versao_min = versao_minima_para(tabela, versao_pdvpro)
    if not versao_min:
        return {
            "ok": True, "bloqueado": False,
            "versao_min": None, "versao_atual": versao_integrador, "aviso": None,
        }

    if not versao_integrador:
        return {
            "ok": False,
            "bloqueado": True,
            "versao_min": versao_min,
            "versao_atual": None,
            "aviso": (
                f"PDVPro {versao_pdvpro} requer VRIntegradorMaster ≥ {versao_min}. "
                "Não foi possível detectar a versão atual do integrador via SSH — "
                "verifique manualmente antes de atualizar."
            ),
        }

    try:
        compativel = versao_para_tupla(versao_integrador) >= versao_para_tupla(versao_min)
    except (ValueError, AttributeError):
        return {
            "ok": True, "bloqueado": False,
            "versao_min": versao_min, "versao_atual": versao_integrador, "aviso": None,
        }

    if compativel:
        return {
            "ok": True, "bloqueado": False,
            "versao_min": versao_min, "versao_atual": versao_integrador, "aviso": None,
        }

    return {
        "ok": False,
        "bloqueado": True,
        "versao_min": versao_min,
        "versao_atual": versao_integrador,
        "aviso": (
            f"PDVPro {versao_pdvpro} requer VRIntegradorMaster ≥ {versao_min}, "
            f"mas a versão instalada é {versao_integrador}. "
            "Atualize o integrador antes de prosseguir."
        ),
    }
