#!/usr/bin/env python3
# scripts/scraper_biblioteca.py — Descarga e ingesta documentos SERCOP
#
# Fuentes:
#   1. Manuales SOCE (URLs directas)
#   2. Normativa principal (URLs directas)
#   3. Scraping de portal.compraspublicas.gob.ec/sercop/biblioteca/
#   4. Fuentes externas: COA (lexis.com.ec), Resolución montos PIE
#
# Uso:
#   cd /Users/mauricioruiz/emmabot/whatsapp-agentkit
#   source /Users/mauricioruiz/emmabot/.venv/bin/activate
#   python scripts/scraper_biblioteca.py

import asyncio
import sys
import os
import re
import time
import logging
from pathlib import Path
from urllib.parse import urljoin, urlparse
from collections import defaultdict

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ── Setup ─────────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("scraper")

BIBLIOTECA_DIR = Path("knowledge/biblioteca")
BIBLIOTECA_DIR.mkdir(parents=True, exist_ok=True)

DOWNLOAD_TIMEOUT = 60.0   # segundos por PDF
SCRAPE_DELAY    = 1.5     # segundos entre requests al portal
MAX_PDF_SIZE_MB = 50      # ignorar PDFs > 50MB

# ── Fuentes definidas ─────────────────────────────────────────────────────────

FUENTE_1_MANUALES_SOCE = [
    {
        "url": "https://portal.compraspublicas.gob.ec/sercop/wp-content/uploads/2023/09/Manual-SOCE-Subasta-inversa-Electronica-bienes-o-servicios-Entidades-Contratantes-signed-signed-signed.pdf",
        "nombre": "Manual SOCE — Subasta Inversa Electrónica Entidades Contratantes",
        "tipo": "manual_soce",
    },
    {
        "url": "https://portal.compraspublicas.gob.ec/sercop/wp-content/uploads/2023/09/Manual-SOCE-Menor-Cuantia-para-bienes-y-servicios-_-Entidades-Contratantes-signed-signed-signed.pdf",
        "nombre": "Manual SOCE — Menor Cuantía Bienes y Servicios Entidades Contratantes",
        "tipo": "manual_soce",
    },
    {
        "url": "https://portal.compraspublicas.gob.ec/sercop/wp-content/uploads/2023/09/Manual-SOCE-Menor-Cuantia-obras_-Entidad-Contratante-signed-signed-signed.pdf",
        "nombre": "Manual SOCE — Menor Cuantía Obras Entidad Contratante",
        "tipo": "manual_soce",
    },
    {
        "url": "https://portal.compraspublicas.gob.ec/sercop/wp-content/uploads/2023/09/Manual-para-publicación-de-contrataciones-infima-cuantía-signed-signed-signed.pdf",
        "nombre": "Manual SOCE — Ínfima Cuantía",
        "tipo": "manual_soce",
    },
    {
        "url": "https://portal.compraspublicas.gob.ec/sercop/wp-content/uploads/2023/09/MANUAL-DE-REGISTRO-COMO-PROVEEDOR-DEL-ESTADO-signed-signed-signed.pdf",
        "nombre": "Manual SOCE — Registro como Proveedor del Estado",
        "tipo": "manual_soce",
    },
    {
        "url": "https://portal.compraspublicas.gob.ec/sercop/wp-content/uploads/2023/09/Manual-fase-contractual-bienes-y-servicios-SOCE-signed-signed-signed.pdf",
        "nombre": "Manual SOCE — Fase Contractual Bienes y Servicios",
        "tipo": "manual_soce",
    },
    {
        "url": "https://portal.compraspublicas.gob.ec/sercop/wp-content/uploads/2023/09/Manual-SOCE-para-la-publicacion-de-contrataciones-en-situaciones-de-Emergencia-Entidades-Contratantes-signed-signed-signed.pdf",
        "nombre": "Manual SOCE — Contrataciones en Situaciones de Emergencia",
        "tipo": "manual_soce",
    },
    {
        "url": "https://portal.compraspublicas.gob.ec/sercop/wp-content/uploads/2023/09/Manual-SOCE-Feria-inclusiva-Entidades-Contratantes-signed-signed-signed.pdf",
        "nombre": "Manual SOCE — Feria Inclusiva Entidades Contratantes",
        "tipo": "manual_soce",
    },
]

FUENTE_2_NORMATIVA = [
    {
        "url": "https://portal.compraspublicas.gob.ec/sercop/wp-content/uploads/2025/05/normativa-secundaria-actualizada-1.pdf",
        "nombre": "Normativa Secundaria de Contratación Pública 2025",
        "tipo": "normativa_secundaria",
    },
    {
        "url": "https://portal.compraspublicas.gob.ec/sercop/wp-content/uploads/2025/12/Reglamento-LOSNCP-20251030.pdf",
        "nombre": "Reglamento General LOSNCP — octubre 2025",
        "tipo": "reglamento",
    },
]

# FUENTE 3: secciones de la biblioteca a scrapear
BIBLIOTECA_URL = "https://portal.compraspublicas.gob.ec/sercop/biblioteca/"

# Secciones a incluir (texto exacto o fragmento en el título del tab/sección)
SECCIONES_INCLUIR = {
    "losncp": "ley",
    "ley orgánica": "ley",
    "resoluciones externas": "resolucion",
    "resoluciones de emergencia": "resolucion",
    "manuales": "manual_soce",
}

# Secciones a OMITIR (si el título las contiene, saltar)
SECCIONES_OMITIR = {
    "pac", "adjudicaciones", "libros", "estadísticas",
    "estadisticas", "archivo histórico", "archivo historico",
}

# FUENTE 4: fuentes externas para COA y resolución PIE
FUENTE_4_EXTERNOS = [
    # Código Orgánico Administrativo — capítulos de recursos administrativos
    # Intentaremos varios mirrors públicos
    {
        "url": "https://www.defensa.gob.ec/wp-content/uploads/downloads/2018/09/Codigo-Organico-Administrativo-COA.pdf",
        "nombre": "Código Orgánico Administrativo — COA",
        "tipo": "ley",
    },
    {
        "url": "https://www.registroficial.gob.ec/index.php/registro-oficial-web/publicaciones/suplementos/item/download/9069_a21f08d29e3acfdee67fd6a35c3e562e",
        "nombre": "Código Orgánico Administrativo — COA (Registro Oficial)",
        "tipo": "ley",
    },
    # Resolución montos PIE 2025 — catálogo de normativas SERCOP
    {
        "url": "https://portal.compraspublicas.gob.ec/sercop/cat_normativas/nor_res_ext/",
        "nombre": "Catálogo Resoluciones Externas SERCOP — montos PIE",
        "tipo": "resolucion",
        "es_pagina_html": True,  # scraping, no descarga directa
    },
]


# ── Utilidades de descarga ────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,text/html,application/xhtml+xml,*/*",
}


def _nombre_archivo_desde_url(url: str) -> str:
    """Extrae un nombre de archivo limpio desde la URL."""
    path_part = urlparse(url).path
    nombre = Path(path_part).name
    # Remover sufijos -signed, signed, etc.
    nombre = re.sub(r"(-signed)+", "", nombre, flags=re.IGNORECASE)
    nombre = re.sub(r"\s+", "_", nombre)
    return nombre[:120] or "documento.pdf"


async def _descargar_pdf(
    client: httpx.AsyncClient,
    url: str,
    dest_dir: Path,
    nombre_archivo: str | None = None,
) -> tuple[Path | None, str | None]:
    """
    Descarga un PDF a dest_dir.
    Retorna (ruta_local, None) o (None, mensaje_error).
    """
    nombre_archivo = nombre_archivo or _nombre_archivo_desde_url(url)
    if not nombre_archivo.lower().endswith(".pdf"):
        nombre_archivo += ".pdf"

    dest = dest_dir / nombre_archivo
    if dest.exists() and dest.stat().st_size > 1024:
        logger.info(f"  Ya descargado: {nombre_archivo}")
        return dest, None

    try:
        resp = await client.get(url, headers=HEADERS, timeout=DOWNLOAD_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        if "html" in content_type and b"<html" in resp.content[:200].lower():
            return None, f"La URL devolvió HTML, no PDF: {url}"

        size_mb = len(resp.content) / (1024 * 1024)
        if size_mb > MAX_PDF_SIZE_MB:
            return None, f"PDF demasiado grande ({size_mb:.1f} MB), omitido"

        if len(resp.content) < 1024:
            return None, f"Respuesta muy pequeña ({len(resp.content)} bytes), posible error"

        dest.write_bytes(resp.content)
        logger.info(f"  ✓ Descargado: {nombre_archivo} ({size_mb:.1f} MB)")
        return dest, None

    except httpx.HTTPStatusError as e:
        return None, f"HTTP {e.response.status_code}: {url}"
    except httpx.TimeoutException:
        return None, f"Timeout descargando: {url}"
    except Exception as e:
        return None, f"Error: {e} — {url}"


# ── Scraping biblioteca ───────────────────────────────────────────────────────

async def _scrapear_biblioteca(client: httpx.AsyncClient) -> list[dict]:
    """
    Extrae enlaces a PDFs de la página biblioteca del SERCOP,
    filtrando por secciones permitidas y omitiendo las excluidas.
    Retorna lista de {url, nombre, tipo}.
    """
    encontrados = []
    try:
        resp = await client.get(BIBLIOTECA_URL, headers=HEADERS, timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        logger.error(f"No se pudo acceder a biblioteca: {e}")
        return []

    soup = BeautifulSoup(html, "lxml")

    # Buscar secciones/tabs que contengan PDFs
    # La biblioteca usa tabs o acordeones — buscamos por heading + links
    processed_urls = set()

    def _tipo_para_seccion(texto_seccion: str) -> str | None:
        texto_lower = texto_seccion.lower()
        for excluido in SECCIONES_OMITIR:
            if excluido in texto_lower:
                return None
        for clave, tipo in SECCIONES_INCLUIR.items():
            if clave in texto_lower:
                return tipo
        return None

    # Estrategia 1: buscar tabs/paneles con data-* attributes
    for panel in soup.find_all(["div", "section", "article"], id=True):
        panel_id = panel.get("id", "").lower()
        tipo = _tipo_para_seccion(panel_id)
        if tipo is None and not any(c in panel_id for c in ["tab", "panel", "content", "collapse"]):
            continue

        # Buscar el heading asociado para determinar el tipo
        heading = panel.find(["h1", "h2", "h3", "h4", "h5", "li"])
        if heading and tipo is None:
            tipo = _tipo_para_seccion(heading.get_text())
        if tipo is None:
            tipo = "resolucion"  # fallback razonable para biblioteca SERCOP

        for a in panel.find_all("a", href=True):
            href = a["href"]
            if ".pdf" not in href.lower():
                continue
            full_url = urljoin(BIBLIOTECA_URL, href)
            if full_url in processed_urls:
                continue
            processed_urls.add(full_url)
            nombre_link = a.get_text(strip=True) or _nombre_archivo_desde_url(full_url)
            encontrados.append({"url": full_url, "nombre": nombre_link[:200], "tipo": tipo})

    # Estrategia 2: heading + siguiente contenedor de links (estructura común en WordPress)
    for heading in soup.find_all(["h2", "h3", "h4"]):
        texto_heading = heading.get_text(strip=True)
        tipo = _tipo_para_seccion(texto_heading)
        if tipo is None:
            continue

        # Buscar links PDF en los siblings/children del heading
        contenedor = heading.find_next_sibling(["div", "ul", "table"]) or heading.parent
        if not contenedor:
            continue
        for a in contenedor.find_all("a", href=True):
            href = a["href"]
            if ".pdf" not in href.lower():
                continue
            full_url = urljoin(BIBLIOTECA_URL, href)
            if full_url in processed_urls:
                continue
            processed_urls.add(full_url)
            nombre_link = a.get_text(strip=True) or _nombre_archivo_desde_url(full_url)
            encontrados.append({"url": full_url, "nombre": nombre_link[:200], "tipo": tipo})

    # Estrategia 3: buscar TODOS los links PDF y clasificar por URL
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if ".pdf" not in href.lower():
            continue
        full_url = urljoin(BIBLIOTECA_URL, href)
        if full_url in processed_urls:
            continue
        processed_urls.add(full_url)

        # Determinar tipo por contexto de URL o texto
        url_lower = full_url.lower()
        nombre_link = a.get_text(strip=True) or _nombre_archivo_desde_url(full_url)

        if any(x in url_lower for x in ["pac", "adjudicacion", "estadistica", "libro", "historico"]):
            continue
        if any(x in nombre_link.lower() for x in ["pac ", "adjudicacion", "estadística", "libro", "histórico"]):
            continue

        if "losncp" in url_lower or "ley-organica" in url_lower:
            tipo = "ley"
        elif "reglamento" in url_lower:
            tipo = "reglamento"
        elif "resolucion" in url_lower or "re-sercop" in url_lower:
            tipo = "resolucion"
        elif "manual" in url_lower:
            tipo = "manual_soce"
        else:
            tipo = "resolucion"  # default para biblioteca SERCOP

        encontrados.append({"url": full_url, "nombre": nombre_link[:200], "tipo": tipo})

    logger.info(f"Biblioteca: {len(encontrados)} PDFs encontrados")
    return encontrados


# ── Búsqueda resolución PIE ───────────────────────────────────────────────────

async def _buscar_resolucion_pie(client: httpx.AsyncClient) -> list[dict]:
    """
    Busca la resolución de montos PIE 2025/2026 en el catálogo de normativas SERCOP.
    """
    encontrados = []
    url_catalogo = "https://portal.compraspublicas.gob.ec/sercop/cat_normativas/nor_res_ext/"
    try:
        resp = await client.get(url_catalogo, headers=HEADERS, timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # Buscar links que mencionen "PIE", "presupuesto", "montos", "umbrales"
        keywords = ["pie", "presupuesto inicial", "montos", "umbral", "2025", "2026"]
        processed = set()
        for a in soup.find_all("a", href=True):
            nombre = a.get_text(strip=True).lower()
            href = a["href"].lower()
            if not any(kw in nombre or kw in href for kw in keywords):
                continue
            if ".pdf" not in href:
                continue
            full_url = urljoin(url_catalogo, a["href"])
            if full_url in processed:
                continue
            processed.add(full_url)
            encontrados.append({
                "url": full_url,
                "nombre": a.get_text(strip=True)[:200] or "Resolución PIE",
                "tipo": "resolucion",
            })
    except Exception as e:
        logger.warning(f"No se pudo acceder al catálogo de normativas: {e}")

    return encontrados


# ── Pipeline principal ────────────────────────────────────────────────────────

async def main():
    from agent.memory import inicializar_db
    from agent.ingestion import ingestar_archivo

    await inicializar_db()

    stats: dict[str, list] = {
        "descargados": [],
        "ingestados": [],
        "fallidos_descarga": [],
        "fallidos_ingesta": [],
        "ya_existian": [],
    }
    chunks_por_tipo: dict[str, int] = defaultdict(int)
    pdfs_por_fuente: dict[str, int] = defaultdict(int)

    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:

        # ── Fuente 1: Manuales SOCE ───────────────────────────────────────────
        logger.info("=" * 60)
        logger.info("FUENTE 1 — Manuales SOCE (8 URLs directas)")
        logger.info("=" * 60)

        for item in FUENTE_1_MANUALES_SOCE:
            nombre_archivo = _nombre_archivo_desde_url(item["url"])
            ruta, error = await _descargar_pdf(client, item["url"], BIBLIOTECA_DIR, nombre_archivo)
            await asyncio.sleep(SCRAPE_DELAY)

            if error:
                logger.warning(f"  ✗ {item['nombre']}: {error}")
                stats["fallidos_descarga"].append({"nombre": item["nombre"], "error": error, "fuente": "FUENTE_1"})
                continue

            pdfs_por_fuente["FUENTE_1_manuales_soce"] += 1
            stats["descargados"].append({"nombre": item["nombre"], "ruta": str(ruta)})

            resultado = await ingestar_archivo(str(ruta), nombre=item["nombre"], tipo=item["tipo"], url=item["url"])
            _registrar_resultado(resultado, item, stats, chunks_por_tipo)

        # ── Fuente 2: Normativa principal ─────────────────────────────────────
        logger.info("=" * 60)
        logger.info("FUENTE 2 — Normativa principal (2 URLs directas)")
        logger.info("=" * 60)

        for item in FUENTE_2_NORMATIVA:
            nombre_archivo = _nombre_archivo_desde_url(item["url"])
            ruta, error = await _descargar_pdf(client, item["url"], BIBLIOTECA_DIR, nombre_archivo)
            await asyncio.sleep(SCRAPE_DELAY)

            if error:
                logger.warning(f"  ✗ {item['nombre']}: {error}")
                stats["fallidos_descarga"].append({"nombre": item["nombre"], "error": error, "fuente": "FUENTE_2"})
                continue

            pdfs_por_fuente["FUENTE_2_normativa"] += 1
            stats["descargados"].append({"nombre": item["nombre"], "ruta": str(ruta)})

            resultado = await ingestar_archivo(str(ruta), nombre=item["nombre"], tipo=item["tipo"], url=item["url"])
            _registrar_resultado(resultado, item, stats, chunks_por_tipo)

        # ── Fuente 3: Biblioteca SERCOP ───────────────────────────────────────
        logger.info("=" * 60)
        logger.info("FUENTE 3 — Scraping biblioteca.compraspublicas.gob.ec")
        logger.info("=" * 60)

        pdfs_biblioteca = await _scrapear_biblioteca(client)
        logger.info(f"  Encontrados: {len(pdfs_biblioteca)} PDFs en biblioteca")

        # Deduplicar contra ya descargados (por URL)
        urls_fuentes_1_2 = {item["url"] for item in FUENTE_1_MANUALES_SOCE + FUENTE_2_NORMATIVA}
        pdfs_biblioteca = [p for p in pdfs_biblioteca if p["url"] not in urls_fuentes_1_2]
        logger.info(f"  Después de dedup: {len(pdfs_biblioteca)} PDFs nuevos")

        for item in pdfs_biblioteca:
            nombre_archivo = _nombre_archivo_desde_url(item["url"])
            ruta, error = await _descargar_pdf(client, item["url"], BIBLIOTECA_DIR, nombre_archivo)
            await asyncio.sleep(SCRAPE_DELAY)

            if error:
                logger.warning(f"  ✗ {item['nombre'][:60]}: {error}")
                stats["fallidos_descarga"].append({"nombre": item["nombre"], "error": error, "fuente": "FUENTE_3"})
                continue

            pdfs_por_fuente["FUENTE_3_biblioteca"] += 1
            stats["descargados"].append({"nombre": item["nombre"], "ruta": str(ruta)})

            resultado = await ingestar_archivo(str(ruta), nombre=item["nombre"], tipo=item["tipo"], url=item["url"])
            _registrar_resultado(resultado, item, stats, chunks_por_tipo)

        # ── Fuente 4: Externos (COA + Resolución PIE) ─────────────────────────
        logger.info("=" * 60)
        logger.info("FUENTE 4 — Fuentes externas (COA + Resolución PIE)")
        logger.info("=" * 60)

        # Buscar resolución PIE en catálogo SERCOP
        pdfs_pie = await _buscar_resolucion_pie(client)
        logger.info(f"  Resoluciones PIE encontradas: {len(pdfs_pie)}")
        for item in pdfs_pie:
            nombre_archivo = _nombre_archivo_desde_url(item["url"])
            ruta, error = await _descargar_pdf(client, item["url"], BIBLIOTECA_DIR, nombre_archivo)
            await asyncio.sleep(SCRAPE_DELAY)
            if error:
                stats["fallidos_descarga"].append({"nombre": item["nombre"], "error": error, "fuente": "FUENTE_4_pie"})
                continue
            pdfs_por_fuente["FUENTE_4_pie"] += 1
            stats["descargados"].append({"nombre": item["nombre"], "ruta": str(ruta)})
            resultado = await ingestar_archivo(str(ruta), nombre=item["nombre"], tipo=item["tipo"], url=item["url"])
            _registrar_resultado(resultado, item, stats, chunks_por_tipo)

        # COA: intentar mirrors
        coa_descargado = False
        for item in FUENTE_4_EXTERNOS:
            if item.get("es_pagina_html"):
                continue
            if coa_descargado:
                break
            logger.info(f"  Intentando COA: {item['url'][:80]}...")
            nombre_archivo = "COA-Codigo-Organico-Administrativo.pdf"
            ruta, error = await _descargar_pdf(client, item["url"], BIBLIOTECA_DIR, nombre_archivo)
            await asyncio.sleep(SCRAPE_DELAY)
            if error:
                logger.warning(f"  ✗ COA mirror falló: {error}")
                stats["fallidos_descarga"].append({"nombre": item["nombre"], "error": error, "fuente": "FUENTE_4_coa"})
            else:
                coa_descargado = True
                pdfs_por_fuente["FUENTE_4_coa"] += 1
                stats["descargados"].append({"nombre": item["nombre"], "ruta": str(ruta)})
                resultado = await ingestar_archivo(str(ruta), nombre=item["nombre"], tipo=item["tipo"], url=item["url"])
                _registrar_resultado(resultado, item, stats, chunks_por_tipo)

    # ── Reporte final ─────────────────────────────────────────────────────────
    _imprimir_reporte(stats, chunks_por_tipo, pdfs_por_fuente)


def _registrar_resultado(resultado: dict, item: dict, stats: dict, chunks_por_tipo: dict):
    tipo = item.get("tipo", "otro")
    if resultado.get("status") == "ok":
        chunks = resultado.get("chunks", 0)
        stats["ingestados"].append({"nombre": item["nombre"], "chunks": chunks, "tipo": tipo})
        chunks_por_tipo[tipo] += chunks
        logger.info(f"  ✓ Ingestado: {item['nombre'][:60]} — {chunks} chunks")
    elif resultado.get("status") == "ya_existia":
        chunks = resultado.get("chunks", 0)
        stats["ya_existian"].append({"nombre": item["nombre"], "chunks": chunks})
        chunks_por_tipo[tipo] += chunks
        logger.info(f"  ↺ Ya existía: {item['nombre'][:60]} — {chunks} chunks")
    else:
        error = resultado.get("detalle", "desconocido")
        stats["fallidos_ingesta"].append({"nombre": item["nombre"], "error": error})
        logger.warning(f"  ✗ Ingesta fallida: {item['nombre'][:60]}: {error}")


def _imprimir_reporte(stats: dict, chunks_por_tipo: dict, pdfs_por_fuente: dict):
    total_desc   = len(stats["descargados"])
    total_ing    = len(stats["ingestados"])
    total_exist  = len(stats["ya_existian"])
    total_f_desc = len(stats["fallidos_descarga"])
    total_f_ing  = len(stats["fallidos_ingesta"])
    total_chunks = sum(chunks_por_tipo.values())

    sep = "=" * 60
    print(f"\n{sep}")
    print("  REPORTE FINAL — scraper_biblioteca.py")
    print(sep)

    print(f"\n📥 PDFs DESCARGADOS POR FUENTE")
    print(f"  {'FUENTE_1_manuales_soce':<35} {pdfs_por_fuente.get('FUENTE_1_manuales_soce', 0):>4}")
    print(f"  {'FUENTE_2_normativa':<35} {pdfs_por_fuente.get('FUENTE_2_normativa', 0):>4}")
    print(f"  {'FUENTE_3_biblioteca':<35} {pdfs_por_fuente.get('FUENTE_3_biblioteca', 0):>4}")
    print(f"  {'FUENTE_4_pie':<35} {pdfs_por_fuente.get('FUENTE_4_pie', 0):>4}")
    print(f"  {'FUENTE_4_coa':<35} {pdfs_por_fuente.get('FUENTE_4_coa', 0):>4}")
    print(f"  {'─' * 39}")
    print(f"  {'TOTAL descargados':<35} {total_desc:>4}")

    print(f"\n🧩 CHUNKS CREADOS POR TIPO")
    for tipo, n in sorted(chunks_por_tipo.items()):
        print(f"  {tipo:<35} {n:>6}")
    print(f"  {'─' * 39}")
    print(f"  {'TOTAL chunks':<35} {total_chunks:>6}")

    print(f"\n📊 RESUMEN INGESTIÓN")
    print(f"  Documentos nuevos ingestados:   {total_ing}")
    print(f"  Documentos ya existentes:       {total_exist}")
    print(f"  Fallos en descarga:             {total_f_desc}")
    print(f"  Fallos en ingesta:              {total_f_ing}")

    if stats["fallidos_descarga"]:
        print(f"\n⚠️  FALLOS EN DESCARGA")
        for f in stats["fallidos_descarga"]:
            print(f"  [{f['fuente']}] {f['nombre'][:55]}")
            print(f"         → {f['error'][:100]}")

    if stats["fallidos_ingesta"]:
        print(f"\n⚠️  FALLOS EN INGESTA")
        for f in stats["fallidos_ingesta"]:
            print(f"  {f['nombre'][:55]}")
            print(f"    → {f['error'][:100]}")

    print(f"\n{sep}\n")


if __name__ == "__main__":
    t0 = time.time()
    asyncio.run(main())
    elapsed = time.time() - t0
    print(f"Tiempo total: {elapsed:.0f}s ({elapsed / 60:.1f} min)\n")
