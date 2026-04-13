# agent/scraper.py — Descarga de documentos públicos del SERCOP

"""
Descarga y pre-procesa documentos normativos públicos del SERCOP Ecuador.

Documentos incluidos:
- LOSNCP (Ley Orgánica del Sistema Nacional de Contratación Pública)
- Reglamento General a la LOSNCP
- Resoluciones vigentes del SERCOP
- Manuales del portal ComprasPúblicas

Uso:
    python -m agent.scraper               # descarga e ingesta todos
    python -m agent.scraper --listar      # solo lista documentos disponibles
"""

import asyncio
import logging
from agent.ingestion import ingestar_url, ingestar_directorio_knowledge

logger = logging.getLogger("agentkit")

# Catálogo de documentos públicos del SERCOP
# Formato: (nombre, tipo, url)
DOCUMENTOS_SERCOP: list[tuple[str, str, str]] = [
    # ── Normativa principal ────────────────────────────────────────────────
    # Fuente espejo verificada (sercop.gob.ec retorna 503 frecuentemente)
    (
        "Ley Orgánica del Sistema Nacional de Contratación Pública (LOSNCP)",
        "ley",
        "https://www.santana.gob.ec/wp-content/uploads/2025/05/LOSNCP.pdf",
    ),
    (
        "Reglamento General a la LOSNCP (RGLOSNCP) — versión 2024",
        "reglamento",
        "https://prodeuteq.gob.ec/wp-content/uploads/2024/04/Reglamento-LOSNCP.pdf",
    ),
    (
        "Normativa Secundaria del Sistema Nacional de Contratación Pública — dic 2024",
        "reglamento",
        "https://www.tce.gob.ec/wp-content/uploads/2024/12/Normativa-Secundaria-del-Sistema-Nacional-de-Contratacion-Publica.pdf",
    ),
    (
        "Reglamento General a la LOSNCP — Secretaría de la Amazonía 2023",
        "reglamento",
        "https://www.secretariadelamazonia.gob.ec/wp-content/uploads/downloads/2023/08/Reglamento-General-a-la-Ley-Organica-del-Sistema-Nacional-de-Contratacion-Publica-LOSNCP.pdf",
    ),
]

# Documentos adicionales que se pueden agregar via variable de entorno
# SERCOP_DOCS_EXTRA=url1,nombre1,tipo1;url2,nombre2,tipo2
import os

def _cargar_docs_extra() -> list[tuple[str, str, str]]:
    raw = os.getenv("SERCOP_DOCS_EXTRA", "")
    if not raw:
        return []
    extras = []
    for entrada in raw.split(";"):
        partes = entrada.strip().split(",", 2)
        if len(partes) == 3:
            url, nombre, tipo = partes
            extras.append((nombre.strip(), tipo.strip(), url.strip()))
    return extras


async def descargar_e_ingestar_todos(verbose: bool = True) -> list[dict]:
    """
    Descarga e ingesta todos los documentos del catálogo SERCOP.
    Saltea los que ya fueron procesados (detección por hash de contenido).
    """
    docs = DOCUMENTOS_SERCOP + _cargar_docs_extra()
    resultados = []

    for nombre, tipo, url in docs:
        if verbose:
            logger.info(f"Procesando: {nombre}")
        try:
            resultado = await ingestar_url(url=url, nombre=nombre, tipo=tipo)
            resultados.append({**resultado, "nombre": nombre, "url": url})
            if verbose:
                status = resultado.get("status", "?")
                chunks = resultado.get("chunks", 0)
                logger.info(f"  → {status} | {chunks} chunks")
        except Exception as e:
            logger.error(f"  → Error ingestando {nombre}: {e}")
            resultados.append({"status": "error", "nombre": nombre, "url": url, "detalle": str(e)})

    # También ingesta archivos locales del directorio /knowledge
    locales = await ingestar_directorio_knowledge("knowledge")
    resultados.extend(locales)

    return resultados


def listar_documentos_disponibles():
    """Imprime el catálogo de documentos disponibles para descargar."""
    print("\n=== Catálogo de Documentos SERCOP ===\n")
    for nombre, tipo, url in DOCUMENTOS_SERCOP:
        print(f"  [{tipo.upper()}] {nombre}")
        print(f"           {url}\n")
    extras = _cargar_docs_extra()
    if extras:
        print("=== Documentos Extra (SERCOP_DOCS_EXTRA) ===\n")
        for nombre, tipo, url in extras:
            print(f"  [{tipo.upper()}] {nombre}")
            print(f"           {url}\n")


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    if "--listar" in sys.argv:
        listar_documentos_disponibles()
    else:
        asyncio.run(descargar_e_ingestar_todos())
