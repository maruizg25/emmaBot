# agent/main.py — Servidor FastAPI del agente SERCOP

"""
Servidor principal del Asistente Virtual SERCOP.
Maneja webhooks de WhatsApp y expone endpoints de administración
para la base de conocimiento (ingestión, documentos, wiki).
"""

import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from agent.brain import generar_respuesta
from agent.memory import inicializar_db, guardar_mensaje, obtener_historial, listar_documentos, eliminar_documento
from agent.providers import obtener_proveedor

load_dotenv()

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
log_level = logging.DEBUG if ENVIRONMENT == "development" else logging.INFO
logging.basicConfig(level=log_level)
logger = logging.getLogger("agentkit")

proveedor = obtener_proveedor()
PORT = int(os.getenv("PORT", 8000))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")  # Dejar vacío desactiva la auth en dev
HISTORIAL_LIMITE = int(os.getenv("HISTORIAL_LIMITE", "4"))  # Mensajes previos al LLM

# Deduplicación de mensajes — evita procesar el mismo mensaje_id dos veces
# Meta reintenta el webhook si no recibe 200 en ~5s (RAG + LLM puede tardar más)
_mensajes_procesados: set[str] = set()
_MAX_IDS_CACHE = 500  # límite para no crecer indefinidamente


def _verificar_admin(request: Request):
    """Verifica el token de administración si está configurado."""
    if not ADMIN_TOKEN:
        return  # Sin token configurado, acceso libre (solo dev)
    token = request.headers.get("X-Admin-Token", "")
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Token de administración requerido")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await inicializar_db()
    logger.info("Base de datos inicializada")
    logger.info(f"Agente SERCOP — SARA corriendo en puerto {PORT}")
    logger.info(f"Proveedor WhatsApp: {proveedor.__class__.__name__}")

    yield


app = FastAPI(
    title="SARA — Asistente Virtual SERCOP",
    description="Sistema de Asesoría y Respuesta Automatizada del SERCOP Ecuador",
    version="2.0.0",
    lifespan=lifespan,
)


# ─── Health check ─────────────────────────────────────────────────────────────

@app.get("/")
async def health_check():
    return {
        "status": "ok",
        "agente": "SARA",
        "organizacion": "SERCOP Ecuador",
        "version": "2.0.0",
    }


# ─── Webhook WhatsApp ─────────────────────────────────────────────────────────

@app.get("/webhook")
async def webhook_verificacion(request: Request):
    """Verificación GET del webhook (Meta Cloud API)."""
    resultado = await proveedor.validar_webhook(request)
    if resultado is not None:
        return PlainTextResponse(str(resultado))
    return {"status": "ok"}


@app.post("/webhook")
async def webhook_handler(request: Request):
    """
    Recibe mensajes de WhatsApp y genera respuesta con contexto SERCOP.
    Pipeline: parse → historial → RAG → Gemma 4 → guardar → enviar
    """
    try:
        mensajes = await proveedor.parsear_webhook(request)
        for msg in mensajes:
            if msg.es_propio or not msg.texto:
                continue

            # Deduplicar por mensaje_id
            if msg.mensaje_id in _mensajes_procesados:
                logger.info(f"Mensaje duplicado ignorado: {msg.mensaje_id}")
                continue
            _mensajes_procesados.add(msg.mensaje_id)
            if len(_mensajes_procesados) > _MAX_IDS_CACHE:
                # Limpiar la mitad más antigua (set no tiene orden, borramos mitad aleatoria)
                ids_a_borrar = list(_mensajes_procesados)[:_MAX_IDS_CACHE // 2]
                for mid in ids_a_borrar:
                    _mensajes_procesados.discard(mid)

            logger.info(f"Mensaje de {msg.telefono}: {msg.texto[:80]}")

            # Acuse inmediato — el usuario sabe que recibimos su mensaje
            _texto_lower = msg.texto.strip().lower()
            _es_saludo = len(msg.texto.split()) <= 3 and not any(
                kw in _texto_lower for kw in ["qué", "que", "cómo", "como", "cuál",
                                               "cual", "art", "rup", "contrat"]
            )
            if not _es_saludo:
                await proveedor.enviar_mensaje(
                    msg.telefono,
                    "🔍 Consultando la normativa SERCOP, un momento..."
                )

            historial = await obtener_historial(msg.telefono, limite=HISTORIAL_LIMITE)
            respuesta = await generar_respuesta(msg.texto, historial)

            await guardar_mensaje(msg.telefono, "user", msg.texto)
            await guardar_mensaje(msg.telefono, "assistant", respuesta)
            await proveedor.enviar_mensaje(msg.telefono, respuesta)

            logger.info(f"SARA respondió a {msg.telefono}")

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error en webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Admin: Base de conocimiento ──────────────────────────────────────────────

@app.get("/admin/documentos")
async def listar_docs(request: Request):
    """Lista todos los documentos en la base de conocimiento."""
    _verificar_admin(request)
    docs = await listar_documentos()
    return {"total": len(docs), "documentos": docs}


@app.delete("/admin/documentos/{documento_id}")
async def eliminar_doc(documento_id: int, request: Request):
    """Elimina un documento y todos sus chunks de la base de conocimiento."""
    _verificar_admin(request)
    await eliminar_documento(documento_id)
    return {"status": "ok", "eliminado": documento_id}


class IngestURLRequest(BaseModel):
    url: str
    nombre: str | None = None
    tipo: str = "otro"


@app.post("/admin/ingestar/url")
async def ingestar_desde_url(body: IngestURLRequest, request: Request):
    """Descarga e ingesta un documento desde una URL."""
    _verificar_admin(request)
    from agent.ingestion import ingestar_url
    resultado = await ingestar_url(url=body.url, nombre=body.nombre, tipo=body.tipo)
    return resultado


@app.post("/admin/ingestar/archivo")
async def ingestar_archivo_upload(
    request: Request,
    archivo: UploadFile = File(...),
    nombre: str = Form(None),
    tipo: str = Form("otro"),
):
    """Sube e ingesta un archivo (PDF, DOCX, MD, TXT)."""
    _verificar_admin(request)
    import tempfile
    from pathlib import Path
    from agent.ingestion import ingestar_archivo

    suffix = Path(archivo.filename).suffix if archivo.filename else ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await archivo.read())
        tmp_path = tmp.name

    try:
        resultado = await ingestar_archivo(
            ruta=tmp_path,
            nombre=nombre or archivo.filename,
            tipo=tipo,
        )
    finally:
        os.unlink(tmp_path)

    return resultado


@app.post("/admin/ingestar/sercop")
async def ingestar_catalogo_sercop(request: Request):
    """Descarga e ingesta todos los documentos del catálogo oficial SERCOP."""
    _verificar_admin(request)
    from agent.scraper import descargar_e_ingestar_todos
    resultados = await descargar_e_ingestar_todos()
    ok = sum(1 for r in resultados if r.get("status") in ("ok", "ya_existia"))
    errores = [r for r in resultados if r.get("status") == "error"]
    return {"procesados": len(resultados), "exitosos": ok, "errores": errores}


# ─── Admin: Wiki ──────────────────────────────────────────────────────────────

@app.post("/admin/wiki/compilar")
async def compilar_wiki(request: Request):
    """
    Compila la wiki de conocimiento SERCOP a partir de los documentos ingestados.
    Genera archivos .md organizados por tema en knowledge/wiki/.
    """
    _verificar_admin(request)
    from agent.wiki import compilar_wiki_completa
    articulos = await compilar_wiki_completa()
    return {"status": "ok", "articulos_generados": len(articulos), "temas": articulos}


@app.post("/admin/wiki/finetune")
async def exportar_dataset_finetune(request: Request):
    """
    Exporta un dataset Q&A en formato JSONL para fine-tuning con Unsloth.
    Output: knowledge/finetune_dataset.jsonl
    """
    _verificar_admin(request)
    from agent.wiki import exportar_dataset_finetune
    total = await exportar_dataset_finetune()
    return {"status": "ok", "pares_qa": total, "archivo": "knowledge/finetune_dataset.jsonl"}


# ─── Admin: Búsqueda de prueba ────────────────────────────────────────────────

class BusquedaRequest(BaseModel):
    query: str
    top_k: int = 5


@app.post("/admin/buscar")
async def buscar_en_kb(body: BusquedaRequest, request: Request):
    """Prueba la búsqueda RAG directamente — útil para debugging."""
    _verificar_admin(request)
    from agent.retriever import buscar_contexto, formatear_contexto
    chunks = await buscar_contexto(body.query)
    return {
        "query": body.query,
        "chunks_encontrados": len(chunks),
        "contexto": formatear_contexto(chunks),
        "chunks_raw": chunks[:body.top_k],
    }
