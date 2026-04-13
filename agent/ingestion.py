# agent/ingestion.py — Pipeline de ingestión de documentos SERCOP

"""
Procesa documentos (PDF, DOCX, HTML, Markdown) y los indexa en la base
de conocimiento con embeddings vectoriales para búsqueda semántica (RAG).

Estrategia de chunking para documentos legales:
- Respeta límites de artículos ("Art. X", "Artículo X")
- Tamaño objetivo: 800 caracteres por chunk
- Solapamiento: 100 caracteres para no perder contexto entre chunks

Mejoras vs versión anterior:
- Embeddings en batch (BATCH_SIZE=32): ~15x más rápido en ingestión
- Bug fix: _dividir_por_tamaño ya no genera loop infinito en artículos largos
- Imports al nivel de módulo (no lazy) para evitar deadlocks en threads
"""

import asyncio
import os
import re
import logging
from pathlib import Path

import httpx
import fitz  # pymupdf
from docx import Document
from bs4 import BeautifulSoup

from agent.embeddings import generar_embeddings_batch, generar_embedding
from agent.memory import (
    crear_documento,
    guardar_chunk,
    actualizar_total_chunks,
    contar_chunks_documento,
    _is_postgres,
)

logger = logging.getLogger("agentkit")

CHUNK_SIZE      = int(os.getenv("CHUNK_SIZE", "800"))
CHUNK_OVERLAP   = int(os.getenv("CHUNK_OVERLAP", "100"))
SKIP_EMBEDDINGS = os.getenv("SKIP_EMBEDDINGS", "false").lower() == "true"
BATCH_SIZE      = int(os.getenv("EMBED_BATCH_SIZE", "32"))

# Regex para detectar inicio de artículos en normativa ecuatoriana
RE_ARTICULO = re.compile(
    r"(Art(?:ículo)?\.?\s*\d+[\w\-]*\.?\s*[-–—]?\s*[A-ZÁÉÍÓÚÑÜ][^.]{0,80}\.)",
    re.IGNORECASE,
)
RE_CAPITULO = re.compile(
    r"(Cap[ií]tulo\s+[IVXLCDM]+|Título\s+[IVXLCDM]+|Secci[oó]n\s+\d+)",
    re.IGNORECASE,
)


# ─── Extracción de texto ──────────────────────────────────────────────────────

def _extraer_pdf(ruta: str) -> tuple[str, list[tuple[int, str]]]:
    """
    Extrae texto de un PDF usando pymupdf.
    Maneja fuentes embebidas, encodings propietarios y PDFs firmados digitalmente.
    Retorna (texto_completo, [(pagina, texto), ...]).
    """
    doc = fitz.open(ruta)
    paginas = []
    for i, page in enumerate(doc, start=1):
        texto = page.get_text("text") or ""
        paginas.append((i, texto))
    doc.close()
    texto_completo = "\n\n".join(t for _, t in paginas)
    return texto_completo, paginas


def _extraer_docx(ruta: str) -> str:
    doc = Document(ruta)
    return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _extraer_html(contenido_html: str) -> str:
    soup = BeautifulSoup(contenido_html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def _extraer_markdown(ruta: str) -> str:
    with open(ruta, "r", encoding="utf-8") as f:
        return f.read()


# ─── Chunking inteligente ─────────────────────────────────────────────────────

def _detectar_seccion(texto: str, pos: int) -> str | None:
    fragmento = texto[max(0, pos - 500):pos]
    match_art = None
    for m in RE_ARTICULO.finditer(fragmento):
        match_art = m.group(1).strip()
    if match_art:
        return match_art[:200]
    for m in RE_CAPITULO.finditer(fragmento):
        return m.group(1).strip()[:200]
    return None


def _dividir_por_tamaño(texto: str) -> list[str]:
    """Divide texto en chunks del tamaño máximo con solapamiento."""
    if len(texto) <= CHUNK_SIZE:
        return [texto]
    partes = []
    inicio = 0
    while inicio < len(texto):
        fin = inicio + CHUNK_SIZE
        if fin < len(texto):
            # Buscar punto de corte desde la mitad del chunk (evita retroceder)
            buscar_desde = inicio + CHUNK_SIZE // 2
            corte = texto.rfind(". ", buscar_desde, fin)
            if corte == -1:
                corte = texto.rfind("\n", buscar_desde, fin)
            if corte != -1:
                fin = corte + 1
        partes.append(texto[inicio:fin].strip())
        # Garantizar avance mínimo — evita loop infinito
        siguiente = fin - CHUNK_OVERLAP
        inicio = max(siguiente, inicio + 1)
    return [p for p in partes if p]


def _chunkear_texto(
    texto: str,
    paginas: list[tuple[int, str]] | None = None,
) -> list[dict]:
    """
    Divide texto en chunks semánticamente coherentes.

    Para normativa legal: divide por límites de artículos.
    Para texto genérico: divide por párrafos respetando tamaño máximo.
    """
    chunks = []

    texto = re.sub(r"\n{3,}", "\n\n", texto)
    texto = re.sub(r" {2,}", " ", texto)

    # Mapa posición → página (solo para PDFs)
    mapa_pagina: dict[int, int] = {}
    if paginas:
        offset = 0
        for num_pagina, texto_pagina in paginas:
            for i in range(offset, offset + len(texto_pagina)):
                mapa_pagina[i] = num_pagina
            offset += len(texto_pagina) + 2

    def _pagina_en(pos: int) -> int | None:
        return mapa_pagina.get(pos)

    splits = list(RE_ARTICULO.finditer(texto))

    if len(splits) >= 3:
        # Documento legal con artículos detectados
        positions = [m.start() for m in splits] + [len(texto)]
        for i in range(len(splits)):
            fragmento = texto[positions[i]:positions[i + 1]].strip()
            if len(fragmento) < 50:
                continue
            seccion = splits[i].group(1).strip()[:200]
            pagina = _pagina_en(positions[i])
            for sub in _dividir_por_tamaño(fragmento):
                chunks.append({"texto": sub, "seccion": seccion, "pagina": pagina})
    else:
        # Texto genérico: dividir por párrafos
        parrafos = [p.strip() for p in texto.split("\n\n") if p.strip()]
        buffer = ""
        pos_buffer = 0
        for parrafo in parrafos:
            if len(buffer) + len(parrafo) > CHUNK_SIZE and buffer:
                chunks.append({
                    "texto": buffer.strip(),
                    "seccion": _detectar_seccion(texto, pos_buffer),
                    "pagina": _pagina_en(pos_buffer),
                })
                buffer = buffer[-CHUNK_OVERLAP:] + "\n\n" + parrafo
            else:
                buffer += ("\n\n" if buffer else "") + parrafo
            pos_buffer += len(parrafo)
        if buffer.strip():
            chunks.append({
                "texto": buffer.strip(),
                "seccion": _detectar_seccion(texto, pos_buffer),
                "pagina": _pagina_en(pos_buffer),
            })

    return chunks


# ─── Pipeline de ingestión ────────────────────────────────────────────────────

async def ingestar_archivo(
    ruta: str,
    nombre: str | None = None,
    tipo: str = "otro",
    url: str | None = None,
) -> dict:
    """
    Ingesta un archivo local en la base de conocimiento.

    Embeddings se generan en batch (BATCH_SIZE chunks por request a Ollama).
    """
    path = Path(ruta)
    if not path.exists():
        return {"status": "error", "detalle": f"Archivo no encontrado: {ruta}"}

    nombre = nombre or path.stem.replace("_", " ").replace("-", " ").title()
    extension = path.suffix.lower()

    logger.info(f"Ingestando: {nombre} ({extension})")

    paginas = None
    try:
        if extension == ".pdf":
            texto, paginas = _extraer_pdf(str(path))
        elif extension in (".docx", ".doc"):
            texto = _extraer_docx(str(path))
        elif extension in (".md", ".txt"):
            texto = _extraer_markdown(str(path))
        else:
            return {"status": "error", "detalle": f"Formato no soportado: {extension}"}
    except Exception as e:
        logger.error(f"Error extrayendo texto de {ruta}: {e}")
        return {"status": "error", "detalle": str(e)}

    if not texto.strip():
        return {"status": "error", "detalle": "El archivo no contiene texto extraíble"}

    doc_id = await crear_documento(nombre=nombre, tipo=tipo, url=url, contenido=texto)

    chunks_existentes = await contar_chunks_documento(doc_id)
    if chunks_existentes > 0:
        logger.info(f"Documento ya ingestado: {nombre} ({chunks_existentes} chunks)")
        return {"documento_id": doc_id, "chunks": chunks_existentes, "status": "ya_existia"}

    chunks = _chunkear_texto(texto, paginas)
    logger.info(f"  → {len(chunks)} chunks generados")

    metadata_base = {"nombre_doc": nombre, "tipo": tipo, "url": url}
    total_ok = 0

    # Regex para extraer número de artículo del texto del chunk
    RE_ART_EXTRACTOR = re.compile(
        r"\bArt(?:ículo)?\.?\s*(\d+[\w\-]*)",
        re.IGNORECASE,
    )

    # Procesar en batches para minimizar requests a Ollama
    for i in range(0, len(chunks), BATCH_SIZE):
        lote = chunks[i:i + BATCH_SIZE]

        if not SKIP_EMBEDDINGS:
            textos_lote = [c["texto"] for c in lote]
            embeddings_lote = await generar_embeddings_batch(textos_lote)
        else:
            embeddings_lote = [None] * len(lote)

        for j, (chunk, emb) in enumerate(zip(lote, embeddings_lote)):
            # Extraer artículo del texto o de la sección
            texto_busqueda = (chunk.get("seccion") or "") + " " + chunk["texto"][:200]
            m = RE_ART_EXTRACTOR.search(texto_busqueda)
            articulo = f"Art. {m.group(1)}" if m else None

            await guardar_chunk(
                documento_id=doc_id,
                texto=chunk["texto"],
                embedding=emb,
                seccion=chunk.get("seccion"),
                pagina=chunk.get("pagina"),
                metadata={**metadata_base, "chunk_index": i + j, "articulo": articulo},
            )
            total_ok += 1

        logger.info(f"  → {min(i + BATCH_SIZE, len(chunks))}/{len(chunks)} chunks procesados")
        await asyncio.sleep(0)  # cede control al event loop entre batches

    await actualizar_total_chunks(doc_id, total_ok)
    logger.info(f"Ingestión completa: {nombre} — {total_ok} chunks guardados")
    return {"documento_id": doc_id, "chunks": total_ok, "status": "ok"}


async def ingestar_url(url: str, nombre: str | None = None, tipo: str = "otro") -> dict:
    """Descarga una página web o PDF y lo ingesta en la base de conocimiento."""
    logger.info(f"Descargando: {url}")
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": "SERCOP-Bot/1.0"})
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            contenido = response.content
    except Exception as e:
        return {"status": "error", "detalle": f"Error descargando {url}: {e}"}

    nombre = nombre or url.split("/")[-1][:100]

    if "pdf" in content_type or url.lower().endswith(".pdf"):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(contenido)
            tmp_path = tmp.name
        try:
            result = await ingestar_archivo(tmp_path, nombre=nombre, tipo=tipo, url=url)
        finally:
            os.unlink(tmp_path)
        return result

    texto = _extraer_html(contenido.decode("utf-8", errors="replace"))
    doc_id = await crear_documento(nombre=nombre, tipo=tipo, url=url, contenido=texto)
    chunks_existentes = await contar_chunks_documento(doc_id)
    if chunks_existentes > 0:
        return {"documento_id": doc_id, "chunks": chunks_existentes, "status": "ya_existia"}

    chunks = _chunkear_texto(texto)
    metadata_base = {"nombre_doc": nombre, "tipo": tipo, "url": url}
    total_ok = 0

    for i in range(0, len(chunks), BATCH_SIZE):
        lote = chunks[i:i + BATCH_SIZE]
        embeddings_lote = (
            await generar_embeddings_batch([c["texto"] for c in lote])
            if not SKIP_EMBEDDINGS
            else [None] * len(lote)
        )
        for j, (chunk, emb) in enumerate(zip(lote, embeddings_lote)):
            await guardar_chunk(
                documento_id=doc_id,
                texto=chunk["texto"],
                embedding=emb,
                seccion=chunk.get("seccion"),
                pagina=None,
                metadata={**metadata_base, "chunk_index": i + j},
            )
            total_ok += 1

    await actualizar_total_chunks(doc_id, total_ok)
    return {"documento_id": doc_id, "chunks": total_ok, "status": "ok"}


async def ingestar_directorio_knowledge(directorio: str = "knowledge") -> list[dict]:
    """Ingesta todos los archivos del directorio /knowledge automáticamente."""
    path = Path(directorio)
    if not path.exists():
        return []

    extensiones_validas = {".pdf", ".docx", ".md", ".txt"}
    resultados = []

    for archivo in sorted(path.rglob("*")):
        if archivo.suffix.lower() not in extensiones_validas:
            continue
        if archivo.name.startswith("."):
            continue

        nombre_lower = archivo.stem.lower()
        if any(k in nombre_lower for k in ["losncp", "ley", "codigo"]):
            tipo = "ley"
        elif any(k in nombre_lower for k in ["reglamento", "rglosncp"]):
            tipo = "reglamento"
        elif any(k in nombre_lower for k in ["resolucion", "resolución"]):
            tipo = "resolucion"
        elif any(k in nombre_lower for k in ["faq", "preguntas", "frecuentes"]):
            tipo = "faq"
        elif any(k in nombre_lower for k in ["manual", "guia", "guía"]):
            tipo = "manual"
        elif archivo.parent.name == "wiki":
            tipo = "wiki"
        else:
            tipo = "otro"

        resultado = await ingestar_archivo(str(archivo), tipo=tipo)
        resultados.append({**resultado, "archivo": str(archivo)})

    return resultados
