# agent/brain.py — Cerebro del agente SERCOP con RAG + Tool Calling

"""
Pipeline de generación de respuestas de SARA:

  1. RAG — recupera chunks relevantes de sercop_db (pgvector + tsvector)
  2. Tool calling — Gemma puede llamar tools estructuradas (plazos, RUP, tipos)
  3. Loop de ejecución — ejecuta tools y devuelve resultados a Gemma
  4. Respuesta final con citas normativas

Flujo completo:
  mensaje → RAG → [system + contexto + historial + tools] → Gemma
         → si tool_call: ejecutar tool → resultado → Gemma → respuesta final
         → si texto directo: devolver respuesta
"""

from __future__ import annotations

import os
import json
import yaml
import logging
import httpx
from functools import lru_cache
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

OLLAMA_URL    = os.getenv("OLLAMA_URL",    "http://localhost:11434")
OLLAMA_MODEL  = os.getenv("OLLAMA_MODEL",  "gemma4:e2b")
WIKI_DIR      = Path(os.getenv("WIKI_DIR", "knowledge/wiki"))
WIKI_FALLBACK = os.getenv("WIKI_FALLBACK", "true").lower() == "true"
MAX_TOOL_TURNS = 3  # Máximo de rondas de tool calling por respuesta

# Ventana de contexto: el system prompt de SARA tiene ~3500 tokens solo.
# 4096 lo truncaba silenciosamente. 8192 es el mínimo real con RAG + historial corto.
# Subir a 16384 solo si hay GPU disponible.
OLLAMA_NUM_CTX    = int(os.getenv("OLLAMA_NUM_CTX",    "8192"))
OLLAMA_MAX_TOKENS = int(os.getenv("OLLAMA_MAX_TOKENS", "512"))


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


async def _llamar_ollama(
    client: httpx.AsyncClient,
    mensajes: list[dict],
    tools: list[dict] | None = None,
) -> dict:
    """Llamada base a Ollama /api/chat. Retorna el dict de respuesta completo."""
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
    if tools:
        payload["tools"] = tools

    response = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)

    # Modelos pequeños (ej. gemma3:1b) no soportan tool calling → reintentar sin tools
    if response.status_code == 400 and tools:
        logger.warning(f"Modelo {OLLAMA_MODEL} no soporta tools, reintentando sin ellas")
        payload.pop("tools")
        response = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)

    response.raise_for_status()
    return response.json()


async def generar_respuesta(mensaje: str, historial: list[dict]) -> str:
    """
    Genera una respuesta para SARA.

    Pipeline:
      1. Skip RAG en saludos cortos
      2. RAG sobre sercop_db
      3. Fallback wiki local
      4. Construir mensajes con system + contexto + historial
      5. Loop tool calling: Gemma decide → ejecutar tool → devolver resultado
      6. Respuesta final
    """
    if not mensaje or len(mensaje.strip()) < 2:
        return _mensaje_fallback()

    # ── 1. Decidir si activar RAG ─────────────────────────────────────────
    _es_consulta = any(
        kw in mensaje.lower() for kw in [
            "qué", "que", "cómo", "como", "cuál", "cual", "cuánto", "cuanto",
            "cuándo", "cuando", "dónde", "donde", "art", "rup", "pac",
            "sie", "losncp", "contrat", "proveedor", "requisit", "plaz", "monto",
            "infima", "ínfima", "licitaci", "subasta", "menor cuantía", "garantí",
            "garantia", "plazo", "registro", "oferta", "adjudic", "pliego",
            "proceso", "particip", "rechaz", "impugn", "portal", "soce",
        ]
    ) or len(mensaje.split()) >= 5

    # ── 2. RAG ────────────────────────────────────────────────────────────
    contexto_rag = ""
    if _es_consulta:
        try:
            from agent.retriever import recuperar_contexto_formateado
            contexto_rag, num_chunks = await recuperar_contexto_formateado(mensaje)
            if num_chunks > 0:
                logger.info(f"RAG: {num_chunks} chunks recuperados")
        except Exception as e:
            logger.warning(f"RAG no disponible: {e}")

    # ── 3. Fallback wiki ──────────────────────────────────────────────────
    if not contexto_rag and WIKI_FALLBACK:
        contexto_rag = _buscar_en_wiki(mensaje)
        if contexto_rag:
            logger.info("Wiki local usada como fallback")

    # ── 4. Construir mensajes ─────────────────────────────────────────────
    system_base = _system_prompt()
    if contexto_rag:
        system_completo = (
            f"{system_base}\n\n"
            "## Contexto de documentos oficiales SERCOP\n"
            "Usa la información de las siguientes fuentes para responder. "
            "Cita la fuente específica (ej: 'Según el Art. 45 de la LOSNCP...').\n\n"
            f"{contexto_rag}"
        )
    else:
        system_completo = system_base

    mensajes: list[dict] = [{"role": "system", "content": system_completo}]
    for msg in historial:
        mensajes.append({"role": msg["role"], "content": msg["content"]})
    mensajes.append({"role": "user", "content": mensaje})

    # ── 5. Loop tool calling ──────────────────────────────────────────────
    # Las tools solo se envían cuando hay una consulta real.
    # Para saludos simples se omiten (~600 tokens menos → respuesta más rápida).
    from agent.tools import TOOLS_SCHEMA, ejecutar_tool
    tools_activas = TOOLS_SCHEMA if _es_consulta else None

    try:
        timeout = int(os.getenv("OLLAMA_TIMEOUT", "120"))
        async with httpx.AsyncClient(timeout=timeout) as client:
            for turno in range(MAX_TOOL_TURNS):
                data = await _llamar_ollama(client, mensajes, tools=tools_activas)
                msg_respuesta = data.get("message", {})
                tool_calls = msg_respuesta.get("tool_calls", [])

                if not tool_calls:
                    # Respuesta de texto final
                    respuesta = msg_respuesta.get("content", "")
                    modo = "RAG+tools" if contexto_rag else "tools"
                    logger.info(f"Respuesta final (turno {turno+1}, modo: {modo}, {len(respuesta)} chars)")
                    return respuesta

                # Hay tool calls — ejecutar cada una
                mensajes.append({"role": "assistant", **msg_respuesta})

                for tc in tool_calls:
                    fn = tc.get("function", {})
                    nombre_tool = fn.get("name", "")
                    argumentos = fn.get("arguments", {})

                    logger.info(f"Tool call: {nombre_tool}({argumentos})")
                    resultado = ejecutar_tool(nombre_tool, argumentos)
                    logger.info(f"Tool result: {resultado[:120]}")

                    mensajes.append({
                        "role": "tool",
                        "content": resultado,
                    })

            # Si agotamos turnos, pedir respuesta final sin tools
            data = await _llamar_ollama(client, mensajes, tools=None)
            return data.get("message", {}).get("content", _mensaje_error())

    except httpx.ConnectError:
        logger.error("Ollama no disponible. ¿Está corriendo? → ollama serve")
        return _mensaje_error()
    except Exception as e:
        logger.error(f"Error Gemma/Ollama: {e}")
        return _mensaje_error()
