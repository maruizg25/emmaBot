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
from fastapi.responses import PlainTextResponse, HTMLResponse, JSONResponse
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
_MENU_INTERACTIVO_HABILITADO = os.getenv("MENU_INTERACTIVO", "true").lower() == "true"
_FEEDBACK_HABILITADO = os.getenv("FEEDBACK_HABILITADO", "true").lower() == "true"

# Opciones del menú principal — IDs alineados con _MENU_QUERIES en brain.py
_MENU_OPCIONES: list[dict] = [
    {"id": "1", "titulo": "Tipos de procesos", "descripcion": "SIE, licitación, ínfima cuantía, feria inclusiva..."},
    {"id": "2", "titulo": "RUP — Proveedores", "descripcion": "Registro, requisitos y renovación"},
    {"id": "3", "titulo": "Buscar procesos", "descripcion": "Cómo participar en el portal"},
    {"id": "4", "titulo": "Garantías y contratos", "descripcion": "Montos, tipos y plazos"},
    {"id": "5", "titulo": "Normativa", "descripcion": "LOSNCP, Reglamento, Resoluciones"},
]


def _es_respuesta_menu(respuesta: str) -> bool:
    """Detecta si una respuesta es el menú de bienvenida (heurística por contenido)."""
    return (
        "1️⃣" in respuesta and "2️⃣" in respuesta
        and ("Tipos de procesos" in respuesta or "RUP" in respuesta)
    )


async def _enviar_respuesta(telefono: str, respuesta: str) -> None:
    """Envía la respuesta dividida en tantos mensajes como sea necesario.
    Si la respuesta es el menú de bienvenida y el proveedor lo soporta,
    envía una lista interactiva en lugar del texto plano con números."""
    if _MENU_INTERACTIVO_HABILITADO and _es_respuesta_menu(respuesta):
        try:
            ok = await proveedor.enviar_lista_interactiva(
                telefono=telefono,
                cuerpo=(
                    "👋 ¡Hola! Soy *SercoBot*, asistente de contratación pública del SERCOP 🇪🇨\n\n"
                    "Elige un tema o escribe tu pregunta directamente."
                ),
                opciones=_MENU_OPCIONES,
                pie="SERCOP — sercop.gob.ec",
                boton_texto="Ver temas",
            )
            if ok:
                return
            logger.info("Lista interactiva no enviada — fallback a texto")
        except Exception as e:
            logger.warning(f"Error enviando lista interactiva: {e} — fallback a texto")

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


def _es_respuesta_sustantiva(respuesta: str) -> bool:
    """True si vale la pena preguntar al usuario si la respuesta le sirvió.
    Excluye saludos, menús, mensajes de error y respuestas muy cortas."""
    s = respuesta.strip()
    if len(s) < 200:
        return False
    if _es_respuesta_menu(s):
        return False
    bandera_error = ("dificultades técnicas" in s) or ("problema técnico" in s)
    return not bandera_error


async def _enviar_feedback_buttons(telefono: str) -> None:
    """Envía botones 👍/👎 después de una respuesta sustantiva."""
    try:
        await proveedor.enviar_botones_interactivos(
            telefono=telefono,
            cuerpo="¿Te fue útil la respuesta?",
            botones=[
                {"id": "fb_si", "titulo": "👍 Sí"},
                {"id": "fb_no", "titulo": "👎 No"},
            ],
        )
    except Exception as e:
        logger.debug(f"No se pudo enviar botones de feedback: {e}")


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

        if _FEEDBACK_HABILITADO and _es_respuesta_sustantiva(respuesta):
            await _enviar_feedback_buttons(telefono)

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


@app.get("/admin/metricas")
async def metricas_json(request: Request, dias: int = 7):
    """Métricas para el dashboard (JSON)."""
    _verificar_admin(request)
    from agent.memory import metricas_dashboard
    return await metricas_dashboard(dias=dias)


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="es"><head>
<meta charset="UTF-8"><title>SercoBot — Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0"></script>
<style>
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0; background: #f5f6f8; color: #1c1c1e; }
  header { background: #0066cc; color: #fff; padding: 16px 24px; }
  header h1 { margin: 0; font-size: 20px; }
  .subtitle { opacity: 0.8; font-size: 13px; }
  .container { max-width: 1280px; margin: 0 auto; padding: 16px; }
  .row { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; margin-bottom: 12px; }
  .kpi { background: #fff; border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
  .kpi h3 { margin: 0 0 8px; font-size: 12px; text-transform: uppercase; color: #6b7280; letter-spacing: 0.5px; }
  .kpi .value { font-size: 28px; font-weight: 600; }
  .kpi .delta { font-size: 12px; color: #6b7280; }
  .card { background: #fff; border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); margin-bottom: 12px; }
  .card h2 { margin: 0 0 12px; font-size: 15px; color: #1c1c1e; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { padding: 8px; text-align: left; border-bottom: 1px solid #eee; }
  th { background: #f8f9fa; font-weight: 600; color: #6b7280; text-transform: uppercase; font-size: 11px; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  @media (max-width: 768px) { .grid-2 { grid-template-columns: 1fr; } }
  select, button { padding: 6px 12px; border: 1px solid #d1d5db; border-radius: 6px; background: #fff; cursor: pointer; }
  button.primary { background: #0066cc; color: #fff; border: none; }
</style>
</head><body>
<header>
  <h1>SercoBot — Dashboard de uso</h1>
  <div class="subtitle">Métricas en vivo · SERCOP / DIO</div>
</header>
<div class="container">
  <div style="margin-bottom: 12px;">
    <label>Rango: </label>
    <select id="rango">
      <option value="1">Hoy</option>
      <option value="7" selected>7 días</option>
      <option value="30">30 días</option>
    </select>
    <button class="primary" onclick="cargar()">Actualizar</button>
  </div>

  <div class="row" id="kpis"></div>

  <div class="card"><h2>Mensajes por día</h2><canvas id="serieChart" height="80"></canvas></div>

  <div class="grid-2">
    <div class="card"><h2>Routing — quién responde</h2><canvas id="routingChart" height="180"></canvas></div>
    <div class="card"><h2>Distribución horaria</h2><canvas id="horarioChart" height="180"></canvas></div>
  </div>

  <div class="grid-2">
    <div class="card"><h2>Latencia por proveedor LLM</h2>
      <table id="tablaLatencia"><thead><tr>
        <th>Proveedor</th><th class="num">n</th><th class="num">p50 ms</th><th class="num">p95 ms</th><th class="num">máx ms</th>
      </tr></thead><tbody></tbody></table>
    </div>
    <div class="card"><h2>Tipos de shortcut</h2>
      <table id="tablaShortcut"><thead><tr>
        <th>Tipo</th><th class="num">Total</th>
      </tr></thead><tbody></tbody></table>
    </div>
  </div>
</div>

<script>
let serieChart, routingChart, horarioChart;

async function cargar() {
  const dias = document.getElementById("rango").value;
  const headers = { "X-Admin-Token": (window.localStorage.getItem("admin_token") || "") };
  const r = await fetch(`/admin/metricas?dias=${dias}`, { headers });
  if (!r.ok) {
    alert("Error cargando métricas: " + r.status);
    return;
  }
  const d = await r.json();
  pintarKPIs(d);
  pintarSerie(d.serie_temporal);
  pintarRouting(d.shortcut_breakdown, d.latencia_por_proveedor);
  pintarHorario(d.distribucion_horaria);
  pintarLatencia(d.latencia_por_proveedor);
  pintarShortcut(d.shortcut_breakdown);
}

function pintarKPIs(d) {
  const csat = d.feedback.csat_pct !== null ? d.feedback.csat_pct + "%" : "—";
  const totalShortcuts = (d.shortcut_breakdown || []).reduce((a, x) => a + x.total, 0);
  const pctShortcut = d.total_consultas ? Math.round(totalShortcuts / d.total_consultas * 100) : 0;
  document.getElementById("kpis").innerHTML = `
    <div class="kpi"><h3>Total consultas</h3><div class="value">${d.total_consultas}</div><div class="delta">últimos ${d.dias} días</div></div>
    <div class="kpi"><h3>% Shortcut (sin LLM)</h3><div class="value">${pctShortcut}%</div><div class="delta">${totalShortcuts} de ${d.total_consultas}</div></div>
    <div class="kpi"><h3>Tasa de error</h3><div class="value">${d.tasa_error_pct}%</div><div class="delta">${d.errores} timeouts</div></div>
    <div class="kpi"><h3>Satisfacción (CSAT)</h3><div class="value">${csat}</div><div class="delta">${d.feedback.total} respuestas</div></div>
  `;
}

function pintarSerie(serie) {
  const ctx = document.getElementById("serieChart");
  if (serieChart) serieChart.destroy();
  serieChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: serie.map(x => x.dia),
      datasets: [
        { label: "Total", data: serie.map(x => x.total), borderColor: "#0066cc", tension: 0.3, fill: false },
        { label: "Shortcuts", data: serie.map(x => x.shortcuts), borderColor: "#10b981", tension: 0.3, fill: false },
        { label: "Errores", data: serie.map(x => x.errores), borderColor: "#ef4444", tension: 0.3, fill: false },
      ],
    },
    options: { responsive: true, plugins: { legend: { position: "top" } } },
  });
}

function pintarRouting(shortcuts, latencia) {
  const labels = latencia.map(x => x.proveedor);
  const data = latencia.map(x => x.n);
  const ctx = document.getElementById("routingChart");
  if (routingChart) routingChart.destroy();
  routingChart = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels,
      datasets: [{
        data,
        backgroundColor: ["#10b981", "#0066cc", "#f59e0b", "#8b5cf6", "#ef4444", "#6b7280", "#84cc16", "#06b6d4", "#ec4899", "#a78bfa", "#f97316"],
      }],
    },
    options: { responsive: true, plugins: { legend: { position: "right" } } },
  });
}

function pintarHorario(horas) {
  const labels = Array.from({length: 24}, (_, i) => String(i).padStart(2, "0") + ":00");
  const map = Object.fromEntries(horas.map(x => [x.hora, x.total]));
  const data = labels.map((_, i) => map[i] || 0);
  const ctx = document.getElementById("horarioChart");
  if (horarioChart) horarioChart.destroy();
  horarioChart = new Chart(ctx, {
    type: "bar",
    data: { labels, datasets: [{ label: "Mensajes", data, backgroundColor: "#0066cc" }] },
    options: { responsive: true, plugins: { legend: { display: false } } },
  });
}

function pintarLatencia(rows) {
  const tbody = document.querySelector("#tablaLatencia tbody");
  tbody.innerHTML = rows.map(r => `<tr>
    <td>${r.proveedor}</td>
    <td class="num">${r.n}</td>
    <td class="num">${r.p50_ms}</td>
    <td class="num">${r.p95_ms}</td>
    <td class="num">${r.max_ms}</td>
  </tr>`).join("");
}

function pintarShortcut(rows) {
  const tbody = document.querySelector("#tablaShortcut tbody");
  tbody.innerHTML = rows.map(r => `<tr>
    <td>${r.tipo}</td><td class="num">${r.total}</td>
  </tr>`).join("");
}

// Bootstrap: pedir token si no está guardado
if (!localStorage.getItem("admin_token")) {
  const t = prompt("Token de administración (deja vacío si no aplica):") || "";
  localStorage.setItem("admin_token", t);
}
cargar();
</script>
</body></html>
"""


@app.get("/admin/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Dashboard HTML con gráficas en vivo."""
    return HTMLResponse(_DASHBOARD_HTML)


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
