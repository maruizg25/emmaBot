#!/usr/bin/env python3
# scripts/scraper_sicm.py — Scraping e ingesta del portal SICM
#
# Extrae texto de las páginas HTML del Sistema Integral de Compras de Medicamentos
# y las ingesta en la base de conocimiento RAG (PostgreSQL + pgvector).
#
# Uso:
#   cd /home/jonathan.ruiz/sara-sercop
#   source .venv/bin/activate
#   python scripts/scraper_sicm.py

import asyncio
import sys
import os
import re
import logging
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("scraper_sicm")

SICM_BASE = "https://compracorporativasalud.compraspublicas.gob.ec/SICM/"
SICM_DIR  = Path("knowledge/sicm")
SICM_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*",
}

# ── Páginas conocidas del SICM ────────────────────────────────────────────────
# Agregar aquí nuevas páginas a medida que se identifiquen.
# Formato: {"url": "...", "nombre": "...", "tipo": "manual_sicm"}

SICM_PAGINAS = [
    # ── Subasta Inversa ───────────────────────────────────────────────────────
    {
        "url": f"{SICM_BASE}?page_id=146",
        "nombre": "SICM — Subasta Inversa: ¿Qué es?",
        "tipo": "manual_sicm",
    },
    {
        "url": f"{SICM_BASE}?page_id=155",
        "nombre": "SICM — Subasta Inversa: Objetivos",
        "tipo": "manual_sicm",
    },
    {
        "url": f"{SICM_BASE}?page_id=159",
        "nombre": "SICM — Subasta Inversa: Alcance",
        "tipo": "manual_sicm",
    },
    {
        "url": f"{SICM_BASE}?page_id=165",
        "nombre": "SICM — Subasta Inversa: Glosario y Abreviaciones",
        "tipo": "manual_sicm",
    },
    # ── Procesos ──────────────────────────────────────────────────────────────
    {
        "url": f"{SICM_BASE}?page_id=171",
        "nombre": "SICM — Procesos: Etapas",
        "tipo": "manual_sicm",
    },
    {
        "url": f"{SICM_BASE}?page_id=175",
        "nombre": "SICM — Procesos: Registro de Proveedores",
        "tipo": "manual_sicm",
    },
    # ── Biblioteca ────────────────────────────────────────────────────────────
    {
        "url": f"{SICM_BASE}?page_id=282",
        "nombre": "SICM — FAQ Preguntas Frecuentes",
        "tipo": "manual_sicm",
    },
    {
        "url": f"{SICM_BASE}?page_id=179",
        "nombre": "SICM — Base Legal",
        "tipo": "manual_sicm",
    },
    {
        "url": f"{SICM_BASE}?page_id=513",
        "nombre": "SICM — Normativa Sanitaria",
        "tipo": "manual_sicm",
    },
    {
        "url": f"{SICM_BASE}?page_id=184",
        "nombre": "SICM — Información de Soporte",
        "tipo": "manual_sicm",
    },
    {
        "url": f"{SICM_BASE}?page_id=188",
        "nombre": "SICM — Manuales",
        "tipo": "manual_sicm",
    },
    # ── Comunicados ───────────────────────────────────────────────────────────
    {
        "url": f"{SICM_BASE}?page_id=447",
        "nombre": "SICM — Comunicados",
        "tipo": "manual_sicm",
    },
    # Videos (page_id=443) omitido — solo contiene links de YouTube
]

# Palabras clave para descubrir nuevas páginas relevantes en el sitio SICM
_KW_RELEVANTES = [
    "sicm", "medicamento", "medicamentos", "fármaco", "farmaco",
    "salud", "compra corporativa", "catálogo", "catalogo",
    "proveedor", "entidad", "proceso", "contratacion", "contratación",
    "registro", "habilitacion", "habilitación", "requisito",
    "oferta", "licitacion", "licitación", "adjudicacion",
    "resolución", "resolucion", "normativa",
]


def _extraer_texto_html(html: str, url: str = "") -> tuple[str, str]:
    """
    Extrae texto limpio y título de una página HTML del SICM.
    Retorna (titulo, texto_limpio).
    """
    soup = BeautifulSoup(html, "lxml")

    # Eliminar elementos de navegación y decorativos
    for tag in soup(["script", "style", "nav", "footer", "header",
                     "aside", "noscript", ".menu", ".sidebar"]):
        tag.decompose()

    # Intentar extraer título
    titulo = ""
    title_tag = soup.find("title")
    if title_tag:
        titulo = title_tag.get_text(strip=True)

    h1 = soup.find("h1")
    if h1:
        titulo = h1.get_text(strip=True) or titulo

    # Contenido principal
    contenido = (
        soup.find("main")
        or soup.find("article")
        or soup.find(id=re.compile(r"content|main|post|entry", re.I))
        or soup.find(class_=re.compile(r"content|main|post|entry", re.I))
        or soup.find("body")
    )

    if contenido:
        texto = contenido.get_text(separator="\n", strip=True)
    else:
        texto = soup.get_text(separator="\n", strip=True)

    # Limpiar líneas vacías consecutivas
    lineas = [l.strip() for l in texto.splitlines()]
    texto = "\n".join(l for l in lineas if l)
    texto = re.sub(r"\n{3,}", "\n\n", texto)

    return titulo, texto


def _es_pagina_relevante(titulo: str, texto: str) -> bool:
    """Filtra páginas de navegación/login sin contenido útil."""
    if len(texto) < 200:
        return False
    texto_lower = (titulo + " " + texto[:500]).lower()
    return any(kw in texto_lower for kw in _KW_RELEVANTES)


async def _descubrir_paginas(client: httpx.AsyncClient) -> list[dict]:
    """
    Crawlea el sitio SICM para descubrir todas las páginas con contenido relevante.
    Sigue links internos a ?page_id=N y /SICM/ruta/.
    """
    descubiertas = []
    page_ids_vistos = {
        parse_qs(urlparse(p["url"]).query).get("page_id", [""])[0]
        for p in SICM_PAGINAS
    }

    logger.info("Descubriendo páginas adicionales en el SICM...")

    try:
        resp = await client.get(SICM_BASE, headers=HEADERS, timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        logger.warning(f"No se pudo acceder al index SICM: {e}")
        return []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        full_url = urljoin(SICM_BASE, href)

        # Solo links internos del SICM
        if "compracorporativasalud" not in full_url:
            continue
        if "/SICM/" not in full_url:
            continue
        # Ignorar archivos
        if re.search(r"\.(pdf|docx?|xlsx?|jpg|png|gif|zip)$", full_url, re.I):
            continue

        parsed = urlparse(full_url)
        page_id = parse_qs(parsed.query).get("page_id", [""])[0]

        if page_id and page_id not in page_ids_vistos:
            page_ids_vistos.add(page_id)
            nombre_link = a.get_text(strip=True)[:120] or f"SICM — Página {page_id}"
            descubiertas.append({
                "url": full_url,
                "nombre": f"SICM — {nombre_link}",
                "tipo": "manual_sicm",
            })

    logger.info(f"  Descubiertas {len(descubiertas)} páginas adicionales")
    return descubiertas


async def _ingestar_pagina_html(
    client: httpx.AsyncClient,
    pagina: dict,
    ingestar_archivo,
) -> dict:
    """
    Descarga una página HTML, extrae texto, guarda como .txt e ingesta.
    """
    url   = pagina["url"]
    nombre = pagina["nombre"]

    # Nombre de archivo seguro
    parsed = urlparse(url)
    page_id = parse_qs(parsed.query).get("page_id", [""])[0]
    slug = re.sub(r"[^\w\-]", "_", parsed.path.strip("/"))
    filename = f"sicm_{page_id or slug}.txt"
    dest = SICM_DIR / filename

    # Si ya existe en disco, reusar
    if dest.exists() and dest.stat().st_size > 200:
        logger.info(f"  Ya descargado: {filename}")
        return await ingestar_archivo(str(dest), nombre=nombre, tipo=pagina["tipo"], url=url)

    try:
        resp = await client.get(url, headers=HEADERS, timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"  ✗ {nombre}: {e}")
        return {"status": "error", "detalle": str(e)}

    titulo, texto = _extraer_texto_html(resp.text, url)

    # Actualizar nombre si se obtuvo del HTML
    if titulo and pagina["nombre"].endswith(("Página 282", "Página 165", "Página 146",
                                              f"Página {page_id}")):
        nombre = f"SICM — {titulo}"
        pagina["nombre"] = nombre

    if not _es_pagina_relevante(titulo, texto):
        logger.info(f"  ⊘ Sin contenido relevante: {nombre[:60]}")
        return {"status": "sin_contenido"}

    dest.write_text(texto, encoding="utf-8")
    logger.info(f"  ✓ Guardado: {filename} ({len(texto):,} chars)")

    return await ingestar_archivo(str(dest), nombre=nombre, tipo=pagina["tipo"], url=url)


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    from agent.memory import inicializar_db
    from agent.ingestion import ingestar_archivo

    await inicializar_db()

    stats = {"ingestados": 0, "ya_existian": 0, "errores": 0, "sin_contenido": 0}

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:

        # Páginas conocidas + descubrimiento automático
        paginas_descubiertas = await _descubrir_paginas(client)
        todas = SICM_PAGINAS + paginas_descubiertas

        logger.info(f"\n{'='*60}")
        logger.info(f"SICM — {len(todas)} páginas a procesar")
        logger.info(f"{'='*60}\n")

        for i, pagina in enumerate(todas, 1):
            logger.info(f"[{i}/{len(todas)}] {pagina['nombre'][:70]}")
            resultado = await _ingestar_pagina_html(client, pagina, ingestar_archivo)

            status = resultado.get("status", "error")
            if status == "ok":
                stats["ingestados"] += 1
                logger.info(
                    f"    ✅ {resultado.get('chunks', 0)} chunks — "
                    f"{resultado.get('documento_id', '')}"
                )
            elif status == "ya_existia":
                stats["ya_existian"] += 1
                logger.info(f"    ⏭ Ya existía ({resultado.get('chunks', 0)} chunks)")
            elif status == "sin_contenido":
                stats["sin_contenido"] += 1
            else:
                stats["errores"] += 1
                logger.warning(f"    ✗ {resultado.get('detalle', 'Error desconocido')}")

            await asyncio.sleep(1.0)  # cortesía al servidor

    logger.info(f"\n{'='*60}")
    logger.info("RESUMEN SICM")
    logger.info(f"  Ingestados:    {stats['ingestados']}")
    logger.info(f"  Ya existían:   {stats['ya_existian']}")
    logger.info(f"  Sin contenido: {stats['sin_contenido']}")
    logger.info(f"  Errores:       {stats['errores']}")
    logger.info(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
