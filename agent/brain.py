# agent/brain.py — Cerebro del agente SERCOP con RAG + Pre-tool execution

"""
Pipeline de generación de respuestas de SARA:

  1. Clasificación del mensaje (saludo / consulta)
  2. Pre-tool execution — Python detecta intención y ejecuta tools sin LLM
  3. RAG — recupera chunks relevantes solo si la pregunta lo requiere
  4. Construir mensaje enriquecido: system estático + user con contexto
  5. Llamada única al LLM — sin tool calling, sin loop

Ventajas sobre el diseño anterior:
  - System prompt estático → Ollama reutiliza KV cache → más rápido
  - No depende de que el modelo soporte tool calling
  - Funciona correctamente con modelos pequeños (gemma3:1b, qwen2.5:3b)
  - Saludos y mensajes simples: 0 RAG, 0 tools → respuesta mínima
"""

from __future__ import annotations

import json
import logging
import os
import yaml
import httpx
from functools import lru_cache
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

# ── Proveedor LLM: "ollama" (local) o "groq" (API externa) ───────────────────
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").lower()

# ── Ollama (local) ────────────────────────────────────────────────────────────
OLLAMA_URL    = os.getenv("OLLAMA_URL",   "http://localhost:11434")
OLLAMA_MODEL  = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")

# Ventana de contexto: el system prompt de SARA tiene ~3500 tokens solo.
# 4096 lo truncaba silenciosamente. 8192 es el mínimo real con RAG + historial corto.
OLLAMA_NUM_CTX    = int(os.getenv("OLLAMA_NUM_CTX",    "8192"))
OLLAMA_MAX_TOKENS = int(os.getenv("OLLAMA_MAX_TOKENS", "400"))

# ── Groq (API externa — gratuita hasta 14.400 req/día) ───────────────────────
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = os.getenv("GROQ_MODEL",   "meta-llama/llama-4-scout-17b-16e-instruct")
GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"

WIKI_DIR      = Path(os.getenv("WIKI_DIR", "knowledge/wiki"))
WIKI_FALLBACK = os.getenv("WIKI_FALLBACK", "true").lower() == "true"


# ─── Configuración cargada una sola vez ──────────────────────────────────────

@lru_cache(maxsize=1)
def _cargar_config() -> dict:
    try:
        with open("config/prompts.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning("config/prompts.yaml no encontrado, usando valores por defecto")
        return {}


def _system_prompt() -> str:
    return _cargar_config().get(
        "system_prompt",
        "Eres SARA, asistente virtual del SERCOP Ecuador. "
        "Respondes preguntas sobre contratación pública citando siempre la normativa vigente.",
    )


def _mensaje_error() -> str:
    return _cargar_config().get(
        "error_message",
        "Lo siento, tuve un problema técnico. Por favor intenta nuevamente o visita www.compraspublicas.gob.ec",
    )


def _mensaje_fallback() -> str:
    return _cargar_config().get(
        "fallback_message",
        "Disculpa, no entendí bien tu consulta. ¿Puedes reformularla?",
    )


# ─── Clasificación del mensaje ────────────────────────────────────────────────

# Saludos y respuestas simples que NO requieren RAG ni tools
_SALUDOS = {
    "hola", "buenas", "buenos días", "buenas tardes", "buenas noches",
    "hi", "hey", "saludos", "buen día", "ola", "hello",
    "gracias", "ok", "okay", "bien", "perfecto", "entendido",
    "de acuerdo", "listo", "claro", "sí", "si", "no", "bye",
    "hasta luego", "adios", "adiós", "chao", "nos vemos",
    "muchas gracias", "excelente", "genial", "muy bien",
    # Saludos sociales con "como" — no son consultas normativas
    "como estas", "cómo estas", "como estás", "cómo estás",
    "como te va", "cómo te va", "como te encuentras", "cómo te encuentras",
    "todo bien", "que tal", "qué tal", "hola como estas", "hola cómo estás",
}

# Palabras clave que indican consulta normativa (activa RAG)
_KW_CONSULTA = {
    "qué", "que", "cómo", "como", "cuál", "cual", "cuánto", "cuanto",
    "cuándo", "cuando", "dónde", "donde", "art", "rup", "pac",
    "sie", "losncp", "contrat", "proveedor", "requisit", "plaz", "monto",
    "infima", "ínfima", "licitaci", "subasta", "cotizaci", "menor cuantía",
    "garantí", "garantia", "plazo", "registro", "oferta", "adjudic",
    "pliego", "proceso", "particip", "rechaz", "impugn", "portal", "soce",
    "pie", "umbral", "valor", "límite", "limite", "compra", "adquisici",
    "obra", "servicio", "bien", "consultor", "emergencia", "régimen",
    "feria", "eps", "mipyme", "contrato", "firma", "garantía",
}


def _clasificar_mensaje(mensaje: str) -> str:
    """
    Clasifica el mensaje en:
      'saludo'   — respuesta rápida, sin RAG ni tools
      'consulta' — activa RAG y pre-tools
    """
    texto = mensaje.strip().lower()

    # Coincidencia exacta con saludo conocido
    if texto in _SALUDOS:
        return "saludo"

    # Mensaje muy corto (≤3 palabras) sin palabras de consulta
    palabras = texto.split()
    if len(palabras) <= 3 and not any(kw in texto for kw in _KW_CONSULTA):
        return "saludo"

    return "consulta"


# ─── Pre-tool execution ───────────────────────────────────────────────────────

def _detectar_tools(mensaje: str) -> list[tuple[str, dict]]:
    """
    Detecta qué tools ejecutar basándose en palabras clave del mensaje.
    Retorna lista de (nombre_tool, argumentos).
    El modelo NO toma esta decisión — es 100% código Python.
    """
    texto = mensaje.lower()
    tools_a_ejecutar: list[tuple[str, dict]] = []

    # ── Montos / umbrales PIE ──────────────────────────────────────────────
    _kw_montos = [
        "monto", "umbral", "cuánto", "cuanto", "pie",
        "límite", "limite", "ínfima", "infima",
        "menor cuantía", "menor cuantia", "cotizaci", "licitaci",
        "cuánto puedo", "cuanto puedo", "hasta cuánto", "hasta cuanto",
        "valor máximo", "valor maximo",
    ]
    if any(kw in texto for kw in _kw_montos):
        tools_a_ejecutar.append(("obtener_montos_pie", {}))

    # ── Plazos (con detección del tipo) ───────────────────────────────────
    _kw_plazos = [
        "plazo", "días", "dias", "cuántos días", "cuantos dias",
        "impugn", "apelar", "apelaci", "recurso",
        "garantía", "garantia", "firmar contrato", "firma contrato",
        "cuándo firmar", "cuando firmar", "tiempo para", "cuánto tarda",
    ]
    if any(kw in texto for kw in _kw_plazos):
        if any(kw in texto for kw in ["impugn", "apelar", "apelaci", "recurso de apelaci"]):
            tipo_plazo = "impugnacion"
        elif any(kw in texto for kw in ["garantía", "garantia", "fiel cumplimiento", "anticipo"]):
            tipo_plazo = "garantias"
        elif any(kw in texto for kw in ["firma", "firmar contrato", "suscripci"]):
            tipo_plazo = "contrato"
        elif any(kw in texto for kw in ["sie", "subasta inversa", "puja"]):
            tipo_plazo = "subasta_inversa"
        elif any(kw in texto for kw in ["menor cuantía", "menor cuantia"]):
            tipo_plazo = "menor_cuantia"
        elif any(kw in texto for kw in ["cotizaci"]):
            tipo_plazo = "cotizacion"
        elif any(kw in texto for kw in ["licitaci"]):
            tipo_plazo = "licitacion"
        else:
            tipo_plazo = "menor_cuantia"  # más común
        tools_a_ejecutar.append(("obtener_plazos", {"tipo": tipo_plazo}))

    # ── RUP / Registro de proveedores ─────────────────────────────────────
    _kw_rup = [
        "rup", "proveedor", "registrar", "registro único", "registro de proveedor",
        "habilitado", "habilitarme", "proveedores del estado", "contratar con el estado",
        "cómo ser proveedor", "como ser proveedor", "requisitos para ser proveedor",
        "inscribir", "inscripción",
    ]
    if any(kw in texto for kw in _kw_rup):
        tools_a_ejecutar.append(("info_rup", {}))

    # ── Fecha / hora actual ───────────────────────────────────────────────
    _kw_fecha = [
        "fecha", "hora", "hoy es", "qué día", "que dia",
        "qué hora", "que hora", "día de hoy", "dia de hoy",
    ]
    if any(kw in texto for kw in _kw_fecha):
        tools_a_ejecutar.append(("obtener_fecha_hora_ecuador", {}))

    # ── Tipo de contratación / proceso recomendado ────────────────────────
    _kw_tipo = [
        "qué proceso", "que proceso", "qué tipo", "que tipo",
        "qué procedimiento", "que procedimiento", "cómo contrato", "como contrato",
        "qué modalidad", "que modalidad", "cuál proceso", "cual proceso",
        "debo usar", "debo contratar", "mejor proceso",
    ]
    if any(kw in texto for kw in _kw_tipo):
        tools_a_ejecutar.append(("recomendar_tipo_contratacion", {"descripcion": mensaje}))

    return tools_a_ejecutar


def _formatear_resultado_tool(nombre: str, resultado_json: str) -> str:
    """Convierte el JSON de una tool en texto legible para el contexto del LLM."""
    try:
        data = json.loads(resultado_json)
    except Exception:
        return resultado_json

    if nombre == "obtener_montos_pie":
        anio = data.get("anio", "")
        pie = data.get("pie", 0)
        lineas = [f"## Umbrales de contratación {anio} (PIE: ${pie:,.0f})"]
        for k, v in data.items():
            if isinstance(v, dict) and "usd" in v:
                usd = v["usd"]
                norma = v.get("normativa", "")
                nombre_tipo = k.replace("_", " ").title()
                usd_str = f"${usd:,.0f}" if isinstance(usd, int) else str(usd)
                lineas.append(f"- {nombre_tipo}: {usd_str} USD ({norma})")
        if "advertencia" in data:
            lineas.append(f"\nNota: {data['advertencia']}")
        return "\n".join(lineas)

    elif nombre == "obtener_plazos":
        tipo = data.get("tipo", "")
        plazos = data.get("plazos", {})
        norma = plazos.pop("normativa", "") if isinstance(plazos, dict) else ""
        nota = plazos.pop("nota", "") if isinstance(plazos, dict) else ""
        lineas = [f"## Plazos: {tipo.replace('_', ' ').title()}"]
        for k, v in (plazos.items() if isinstance(plazos, dict) else []):
            lineas.append(f"- {k.replace('_', ' ').title()}: {v}")
        if norma:
            lineas.append(f"- Normativa: {norma}")
        if nota:
            lineas.append(f"- Nota: {nota}")
        return "\n".join(lineas)

    elif nombre == "info_rup":
        lineas = ["## Registro Único de Proveedores (RUP)"]
        lineas.append(f"- {data.get('descripcion', '')}")
        lineas.append(f"- Normativa: {data.get('normativa', '')}")
        lineas.append(f"- Costo: {data.get('costo', '')}")
        lineas.append(f"- Tiempo: {data.get('tiempo_proceso', '')}")
        lineas.append(f"- Renovación: {data.get('renovacion', '')}")
        req_pn = data.get("requisitos_persona_natural", [])
        if req_pn:
            lineas.append("- Requisitos persona natural: " + "; ".join(req_pn))
        req_pj = data.get("requisitos_persona_juridica", [])
        if req_pj:
            lineas.append("- Requisitos persona jurídica: " + "; ".join(req_pj))
        lineas.append(f"- Portal: {data.get('portal', '')}")
        return "\n".join(lineas)

    elif nombre == "obtener_fecha_hora_ecuador":
        return (
            f"## Fecha y hora actual en Ecuador\n"
            f"- Fecha: {data.get('fecha', '')}\n"
            f"- Hora: {data.get('hora', '')} (UTC-5)\n"
            f"- Día: {data.get('dia_semana', '')}"
        )

    elif nombre == "recomendar_tipo_contratacion":
        lineas = [f"## Proceso recomendado: {data.get('tipo_recomendado', '')}"]
        lineas.append(f"- {data.get('descripcion', '')}")
        lineas.append(f"- Montos: {data.get('montos', '')}")
        lineas.append(f"- Normativa: {data.get('normativa', '')}")
        lineas.append(f"- Ventaja: {data.get('ventaja', '')}")
        return "\n".join(lineas)

    return resultado_json


# ─── Wiki local (fallback sin pgvector) ──────────────────────────────────────

def _buscar_en_wiki(query: str, max_chars: int = 2000) -> str:
    """Fallback: búsqueda en archivos .md de la wiki local."""
    if not WIKI_DIR.exists():
        return ""
    query_lower = query.lower()
    terminos = [t for t in query_lower.split() if len(t) > 3]
    if not terminos:
        return ""
    mejores: list[tuple[int, str, str]] = []
    for md_file in WIKI_DIR.glob("*.md"):
        contenido = md_file.read_text(encoding="utf-8", errors="replace")
        hits = sum(contenido.lower().count(t) for t in terminos)
        if hits > 0:
            mejores.append((hits, md_file.stem, contenido))
    if not mejores:
        return ""
    mejores.sort(reverse=True)
    _, nombre, contenido = mejores[0]
    return f"[WIKI: {nombre.replace('_', ' ').title()}]\n{contenido[:max_chars]}"


# ─── Llamadas al LLM ─────────────────────────────────────────────────────────

async def _llamar_ollama(
    client: httpx.AsyncClient,
    mensajes: list[dict],
) -> str:
    """Llamada a Ollama /api/chat (LLM local). Sin tool calling."""
    payload: dict = {
        "model": OLLAMA_MODEL,
        "messages": mensajes,
        "stream": False,
        "options": {
            "temperature": 0.2,
            "top_p": 0.9,
            "num_ctx": OLLAMA_NUM_CTX,
            "num_predict": OLLAMA_MAX_TOKENS,
            "repeat_penalty": 1.1,
        },
    }
    response = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
    response.raise_for_status()
    data = response.json()
    return data.get("message", {}).get("content", "")


async def _llamar_groq(
    client: httpx.AsyncClient,
    mensajes: list[dict],
) -> str:
    """Llamada a Groq API (OpenAI-compatible). ~1s de respuesta."""
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    # Truncar el último mensaje (user) si el contexto inyectado es muy largo.
    # Groq rechaza con 413 si el payload supera ~6,000 tokens totales en modelos 8b.
    MAX_USER_CHARS = int(os.getenv("GROQ_MAX_USER_CHARS", "6000"))
    mensajes_groq = []
    for i, msg in enumerate(mensajes):
        if msg["role"] == "user" and i == len(mensajes) - 1:
            content = msg["content"]
            if len(content) > MAX_USER_CHARS:
                content = content[:MAX_USER_CHARS] + "\n\n[contexto truncado]"
                logger.warning(f"Contexto truncado a {MAX_USER_CHARS} chars para Groq")
            mensajes_groq.append({"role": "user", "content": content})
        else:
            mensajes_groq.append(msg)

    payload = {
        "model": GROQ_MODEL,
        "messages": mensajes_groq,
        "temperature": 0.2,
        "max_tokens": 600,
    }
    response = await client.post(GROQ_URL, json=payload, headers=headers)

    if response.status_code == 413:
        logger.warning("Groq 413: payload demasiado grande, reintentando sin contexto RAG")
        solo_pregunta = next(
            (m["content"].split("\n\n---\n")[0] for m in reversed(mensajes_groq) if m["role"] == "user"),
            mensajes_groq[-1]["content"]
        )
        payload["messages"] = [mensajes_groq[0], {"role": "user", "content": solo_pregunta}]
        response = await client.post(GROQ_URL, json=payload, headers=headers)

    if response.status_code == 429:
        logger.warning("Groq 429: rate limit alcanzado, usando Ollama local como fallback")
        return await _llamar_ollama(client, mensajes)

    response.raise_for_status()

    # Mostrar en logs cuánto queda del límite gratuito
    h = response.headers
    remaining_req = h.get("x-ratelimit-remaining-requests", "?")
    remaining_tok = h.get("x-ratelimit-remaining-tokens", "?")
    limit_req     = h.get("x-ratelimit-limit-requests", "?")
    reset_req     = h.get("x-ratelimit-reset-requests", "?")
    logger.info(f"Groq límites — requests: {remaining_req}/{limit_req} (reset: {reset_req}) | tokens/min restantes: {remaining_tok}")

    # Advertir cuando queden menos de 100 requests del día
    try:
        if int(remaining_req) < 100:
            logger.warning(f"⚠️  Groq: quedan solo {remaining_req} requests hoy — considera plan de pago")
    except (ValueError, TypeError):
        pass

    data = response.json()
    return data["choices"][0]["message"]["content"]


async def _llamar_llm(client: httpx.AsyncClient, mensajes: list[dict]) -> str:
    """Despacha al proveedor configurado: groq u ollama."""
    if LLM_PROVIDER == "groq":
        if not GROQ_API_KEY:
            logger.warning("GROQ_API_KEY no configurado, usando Ollama como fallback")
            return await _llamar_ollama(client, mensajes)
        return await _llamar_groq(client, mensajes)
    return await _llamar_ollama(client, mensajes)


# ─── Pipeline principal ───────────────────────────────────────────────────────

async def generar_respuesta(mensaje: str, historial: list[dict]) -> str:
    """
    Genera una respuesta para el usuario.

    Pipeline:
      1. Clasificar: saludo vs. consulta
      2. Saludos → llamada mínima al LLM (sin RAG, sin tools)
      3. Consultas → pre-ejecutar tools detectadas por keyword
      4. RAG sobre sercop_db (para preguntas normativas sin datos estructurados)
      5. Construir user message con contexto embebido
      6. Llamada única al LLM (system prompt estático → KV cache de Ollama)
    """
    if not mensaje or len(mensaje.strip()) < 2:
        return _mensaje_fallback()

    tipo_mensaje = _clasificar_mensaje(mensaje)
    system = _system_prompt()  # SIEMPRE el mismo → KV cache de Ollama

    # Historial base (sin el mensaje actual)
    mensajes_base: list[dict] = [{"role": "system", "content": system}]
    for msg in historial:
        mensajes_base.append({"role": msg["role"], "content": msg["content"]})

    # ── Ruta rápida: saludos y mensajes simples ───────────────────────────
    if tipo_mensaje == "saludo":
        mensajes_base.append({"role": "user", "content": mensaje})
        logger.info("Ruta rápida: saludo/mensaje simple (sin RAG ni tools)")
        try:
            timeout = int(os.getenv("OLLAMA_TIMEOUT", "120"))
            async with httpx.AsyncClient(timeout=timeout) as client:
                respuesta = await _llamar_llm(client, mensajes_base)
                logger.info(f"Respuesta saludo ({len(respuesta)} chars)")
                return respuesta
        except httpx.ConnectError:
            logger.error("Ollama no disponible")
            return _mensaje_error()
        except Exception as e:
            logger.error(f"Error Ollama: {e}")
            return _mensaje_error()

    # ── Ruta consulta: pre-tools + RAG ────────────────────────────────────

    # Paso 1: Pre-ejecutar tools detectadas por keyword
    bloques_contexto: list[str] = []

    tools_detectadas = _detectar_tools(mensaje)
    if tools_detectadas:
        from agent.tools import ejecutar_tool
        for nombre_tool, argumentos in tools_detectadas:
            try:
                resultado_json = ejecutar_tool(nombre_tool, argumentos)
                bloque = _formatear_resultado_tool(nombre_tool, resultado_json)
                bloques_contexto.append(bloque)
                logger.info(f"Pre-tool ejecutada: {nombre_tool}")
            except Exception as e:
                logger.warning(f"Error en pre-tool {nombre_tool}: {e}")

    # Paso 2: RAG — para preguntas normativas que necesitan texto de la base
    if not bloques_contexto:
        try:
            from agent.retriever import recuperar_contexto_formateado
            contexto_rag, num_chunks = await recuperar_contexto_formateado(mensaje)
            if num_chunks > 0:
                bloques_contexto.append(
                    f"## Normativa relevante (fuentes SERCOP)\n{contexto_rag}"
                )
                logger.info(f"RAG: {num_chunks} chunks recuperados")
        except Exception as e:
            logger.warning(f"RAG no disponible: {e}")

    # Paso 3: Fallback wiki local
    if not bloques_contexto and WIKI_FALLBACK:
        wiki = _buscar_en_wiki(mensaje)
        if wiki:
            bloques_contexto.append(wiki)
            logger.info("Wiki local usada como fallback")

    # Paso 4: Construir mensaje del usuario con contexto embebido
    if bloques_contexto:
        contexto_texto = "\n\n".join(bloques_contexto)
        mensaje_enriquecido = (
            f"{mensaje}\n\n"
            "---\n"
            "Información de referencia para responder:\n\n"
            f"{contexto_texto}"
        )
    else:
        mensaje_enriquecido = mensaje

    mensajes_base.append({"role": "user", "content": mensaje_enriquecido})

    # Paso 5: Llamada única al LLM
    try:
        timeout = int(os.getenv("OLLAMA_TIMEOUT", "120"))
        async with httpx.AsyncClient(timeout=timeout) as client:
            respuesta = await _llamar_llm(client, mensajes_base)
            modo = "tools" if tools_detectadas else ("RAG" if bloques_contexto else "sin contexto")
            logger.info(f"Respuesta generada (modo: {modo}, {len(respuesta)} chars)")
            return respuesta

    except httpx.ConnectError:
        logger.error("Ollama no disponible. ¿Está corriendo? → ollama serve")
        return _mensaje_error()
    except Exception as e:
        logger.error(f"Error Ollama: {e}")
        return _mensaje_error()
