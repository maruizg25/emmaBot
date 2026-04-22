# agent/main.py — Servidor FastAPI del agente SERCOP

"""
Servidor principal del Asistente Virtual SERCOP.
Maneja webhooks de WhatsApp y expone endpoints de administración
para la base de conocimiento (ingestión, documentos, wiki).
"""

from __future__ import annotations

import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
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
    logger.info(f"Agente SERCOP — SercoBot corriendo en puerto {PORT}")
    logger.info(f"Proveedor WhatsApp: {proveedor.__class__.__name__}")

    yield


app = FastAPI(
    title="SercoBot — Asistente Virtual SERCOP",
    description="SercoBot — Asistente Virtual de Contratación Pública del SERCOP Ecuador",
    version="2.0.0",
    lifespan=lifespan,
    redirect_slashes=False,
)

# Servir PDFs de normativa para descarga y envío por WhatsApp
from pathlib import Path
_KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"
if _KNOWLEDGE_DIR.exists():
    app.mount("/docs", StaticFiles(directory=str(_KNOWLEDGE_DIR)), name="documentos")


# ─── Health check ─────────────────────────────────────────────────────────────

@app.get("/")
async def health_check():
    return {
        "status": "ok",
        "agente": "SercoBot",
        "organizacion": "SERCOP Ecuador",
        "version": "2.0.0",
    }


# ─── Webhook WhatsApp ─────────────────────────────────────────────────────────

@app.get("/webhook/webhook")
@app.get("/webhook")
async def webhook_verificacion(request: Request):
    """Verificación GET del webhook (Meta Cloud API)."""
    resultado = await proveedor.validar_webhook(request)
    if resultado is not None:
        return PlainTextResponse(str(resultado))
    return {"status": "ok"}


_WHATSAPP_MAX_CHARS = 3800  # WhatsApp limita a 4096; dejamos margen


async def _enviar_respuesta(telefono: str, respuesta: str) -> None:
    """Envía la respuesta dividida en tantos mensajes como sea necesario."""
    pendiente = respuesta.strip()
    while pendiente:
        if len(pendiente) <= _WHATSAPP_MAX_CHARS:
            await proveedor.enviar_mensaje(telefono, pendiente)
            break
        # Buscar corte en párrafo cercano al límite
        corte = pendiente.rfind("\n\n", _WHATSAPP_MAX_CHARS - 600, _WHATSAPP_MAX_CHARS)
        if corte == -1:
            corte = pendiente.rfind("\n", _WHATSAPP_MAX_CHARS - 300, _WHATSAPP_MAX_CHARS)
        if corte == -1:
            corte = _WHATSAPP_MAX_CHARS
        await proveedor.enviar_mensaje(telefono, pendiente[:corte].strip())
        pendiente = pendiente[corte:].strip()


async def _responder_multimedia(telefono: str) -> None:
    """Responde a mensajes multimedia (audio, imagen, sticker) con orientación textual."""
    try:
        await proveedor.enviar_mensaje(
            telefono,
            "Recibí tu mensaje 😊 Por ahora solo puedo responder preguntas de texto.\n\n"
            "Escribe tu consulta y te ayudo enseguida. Por ejemplo:\n"
            "• _¿Qué es el RUP?_\n"
            "• _¿Cómo me registro como proveedor?_\n"
            "• _¿Cuáles son los tipos de contratación?_"
        )
    except Exception as e:
        logger.error(f"Error enviando respuesta multimedia a {telefono}: {e}")


def _detectar_solicitud_pdf(texto: str) -> dict | None:
    """Detecta si el usuario está pidiendo un PDF/documento para descargar."""
    texto_lower = texto.lower()
    _kw_descarga = ["descargar", "descarga", "enviar", "enviame", "envíame",
                     "pdf", "archivo", "documento completo", "dame el",
                     "necesito el", "pasame", "pásame", "compartir"]
    if not any(kw in texto_lower for kw in _kw_descarga):
        return None
    from agent.brain import DOCUMENTOS_PDF
    for doc in DOCUMENTOS_PDF.values():
        if any(kw in texto_lower for kw in doc["keywords"]):
            return doc
    return None


async def _procesar_mensaje(telefono: str, texto: str, mensaje_id: str) -> None:
    """
    Procesa un mensaje en background: RAG → LLM → enviar respuesta.
    Se ejecuta después de responder 200 OK a Meta para evitar timeouts.
    """
    try:
        _texto_lower = texto.strip().lower()
        _es_saludo = len(texto.split()) <= 3 and not any(
            kw in _texto_lower for kw in ["qué", "que", "cómo", "como", "cuál",
                                           "cual", "art", "rup", "contrat"]
        )

        # Acuse inmediato — llega al usuario en <1s mientras el LLM trabaja
        if not _es_saludo:
            await proveedor.enviar_mensaje(
                telefono,
                "🔍 Consultando la normativa SERCOP, un momento..."
            )

        # Detectar solicitud de PDF
        doc_solicitado = _detectar_solicitud_pdf(texto)
        if doc_solicitado:
            from agent.brain import BASE_URL
            from urllib.parse import quote
            url_doc = f"{BASE_URL}/docs/{quote(doc_solicitado['archivo'])}"
            await proveedor.enviar_mensaje(telefono, doc_solicitado["caption"])
            enviado = await proveedor.enviar_documento(
                telefono, url_doc, doc_solicitado["nombre"], doc_solicitado["caption"]
            )
            if enviado:
                respuesta = doc_solicitado["caption"] + "\n\n✅ Documento enviado. ¿Tienes alguna pregunta sobre su contenido?"
            else:
                respuesta = (
                    doc_solicitado["caption"] + "\n\n"
                    "No pude enviar el archivo directamente. Puedes descargarlo desde:\n"
                    f"🔗 {url_doc}\n\n"
                    "¿Tienes alguna pregunta sobre su contenido?"
                )
            await guardar_mensaje(telefono, "user", texto)
            await guardar_mensaje(telefono, "assistant", respuesta)
            logger.info(f"SercoBot envió documento a {telefono}: {doc_solicitado['nombre']}")
            return

        historial = await obtener_historial(telefono, limite=HISTORIAL_LIMITE)
        respuesta = await generar_respuesta(texto, historial, telefono=telefono)

        await guardar_mensaje(telefono, "user", texto)
        await guardar_mensaje(telefono, "assistant", respuesta)
        await _enviar_respuesta(telefono, respuesta)

        logger.info(f"SercoBot respondió a {telefono} ({len(respuesta)} chars)")

    except Exception as e:
        import traceback
        logger.error(f"Error procesando mensaje de {telefono}: {e}\n{traceback.format_exc()}")
        try:
            await proveedor.enviar_mensaje(
                telefono,
                "Tuve un problema técnico respondiendo tu consulta 😔 "
                "Por favor intenta de nuevo en un momento, o consulta en "
                "www.compraspublicas.gob.ec o llama al 1800-737267."
            )
        except Exception:
            pass


@app.post("/webhook/webhook")
@app.post("/webhook")
async def webhook_handler(request: Request, background_tasks: BackgroundTasks):
    """
    Recibe mensajes de WhatsApp. Responde 200 OK inmediatamente a Meta
    y procesa el LLM en background para evitar timeouts (Meta espera max 5s).
    """
    try:
        mensajes = await proveedor.parsear_webhook(request)
        for msg in mensajes:
            if msg.es_propio:
                continue
            if not msg.texto:
                # Mensaje multimedia (audio, imagen, sticker) — responde sin pasar al LLM
                background_tasks.add_task(_responder_multimedia, msg.telefono)
                continue

            # Deduplicar por mensaje_id
            if msg.mensaje_id in _mensajes_procesados:
                logger.info(f"Mensaje duplicado ignorado: {msg.mensaje_id}")
                continue
            _mensajes_procesados.add(msg.mensaje_id)
            if len(_mensajes_procesados) > _MAX_IDS_CACHE:
                ids_a_borrar = list(_mensajes_procesados)[:_MAX_IDS_CACHE // 2]
                for mid in ids_a_borrar:
                    _mensajes_procesados.discard(mid)

            logger.info(f"Mensaje de {msg.telefono}: {msg.texto[:80]}")
            background_tasks.add_task(_procesar_mensaje, msg.telefono, msg.texto, msg.mensaje_id)

        # 200 OK inmediato — Meta no espera el LLM
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


# ─── Admin: Chat de prueba (QA) ──────────────────────────────────────────────

@app.post("/admin/chat")
@app.post("/admin/chat/")
async def chat_prueba(request: Request):
    """
    Endpoint síncrono para QA — devuelve la respuesta del bot directamente.
    No envía nada a WhatsApp. Útil para pruebas masivas con SoapUI/Postman/JMeter.
    Acepta cualquier campo: mensaje/message/pregunta/query + telefono/phone + token.
    """
    try:
        data = await request.json()
    except Exception:
        data = {}

    token = data.get("token", "")
    if ADMIN_TOKEN and token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Token inválido")

    mensaje = (
        data.get("mensaje") or data.get("message") or
        data.get("pregunta") or data.get("query") or ""
    ).strip()
    telefono = (data.get("telefono") or data.get("phone") or "test-qa").strip()

    if not mensaje:
        raise HTTPException(status_code=400, detail="Campo 'mensaje' requerido")

    import time
    t0 = time.time()
    historial = await obtener_historial(telefono, limite=HISTORIAL_LIMITE)
    respuesta = await generar_respuesta(mensaje, historial, telefono=telefono)
    await guardar_mensaje(telefono, "user", mensaje)
    await guardar_mensaje(telefono, "assistant", respuesta)
    return {
        "mensaje": mensaje,
        "respuesta": respuesta,
        "telefono": telefono,
        "tiempo_ms": int((time.time() - t0) * 1000),
    }


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


# ─── Admin: Estadísticas ──────────────────────────────────────────────────────

@app.post("/admin/faq/reload")
async def recargar_faq(request: Request):
    """Recarga el faq_cache.yaml en memoria sin reiniciar el servidor."""
    _verificar_admin(request)
    from agent.brain import _cargar_faq_cache, _cargar_config
    _cargar_faq_cache.cache_clear()
    _cargar_config.cache_clear()
    faqs = _cargar_faq_cache()
    return {"status": "ok", "faqs_cargados": len(faqs)}


@app.get("/admin/stats")
async def estadisticas(request: Request):
    """
    Estadísticas de uso del bot:
    - Consultas hoy / semana
    - % ahorro por shortcuts
    - Proveedor LLM más usado
    - Top preguntas frecuentes
    - Cache hits del FAQ
    """
    _verificar_admin(request)
    from agent.memory import obtener_estadisticas
    from agent.brain import _cargar_faq_cache
    stats = await obtener_estadisticas()
    # Top FAQs usados en esta sesión (frecuencia en memoria)
    faqs = _cargar_faq_cache()
    top_faqs = sorted(
        [{"pregunta": f.get("pregunta", ""), "frecuencia": f.get("frecuencia", 0)}
         for f in faqs if f.get("frecuencia", 0) > 0],
        key=lambda x: x["frecuencia"], reverse=True
    )[:10]
    stats["top_faqs_sesion"] = top_faqs
    return stats
