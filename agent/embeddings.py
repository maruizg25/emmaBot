# agent/embeddings.py — Generación de embeddings vectoriales con Ollama

"""
Genera vectores semánticos usando Ollama (nomic-embed-text).
Soporta generación individual y en batch para ingestión eficiente.

Modelo: nomic-embed-text (274 MB, 768 dimensiones)
Instalar: ollama pull nomic-embed-text

Mejoras vs versión anterior:
- Cliente httpx reutilizable (no se crea uno por llamada)
- generar_embeddings_batch(): 1 request HTTP para N textos (~15x más rápido en ingestión)
- Retorna list[float] directamente (no string pg-format)
"""

from __future__ import annotations

import os
import httpx
import logging
from collections import OrderedDict
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

OLLAMA_URL  = os.getenv("OLLAMA_URL",  "http://localhost:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
EMBED_DIM   = int(os.getenv("EMBED_DIM", "768"))

# Cliente compartido — evita overhead de crear TCP connection por cada embedding
_http_client: httpx.AsyncClient | None = None

# Cache LRU de embeddings — evita recalcular para queries repetidas
# Máximo 300 entradas (cada una ~3KB → ~900KB RAM total)
_EMBED_CACHE_MAX = 300
_embed_cache: OrderedDict[str, list[float]] = OrderedDict()


def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=120.0)
    return _http_client


async def generar_embedding(texto: str) -> list[float] | None:
    """
    Genera embedding vectorial para un texto.
    Usa cache LRU en memoria para evitar recalcular queries repetidas.

    Returns:
        Lista de floats (EMBED_DIM dimensiones) o None si hay error.
    """
    # Cache hit
    cache_key = texto[:500]  # truncar para evitar claves enormes
    if cache_key in _embed_cache:
        _embed_cache.move_to_end(cache_key)
        return _embed_cache[cache_key]

    try:
        response = await _get_client().post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": texto},
        )
        response.raise_for_status()
        embedding = response.json()["embedding"]

        # Guardar en cache LRU
        _embed_cache[cache_key] = embedding
        _embed_cache.move_to_end(cache_key)
        if len(_embed_cache) > _EMBED_CACHE_MAX:
            _embed_cache.popitem(last=False)  # elimina el más antiguo

        return embedding
    except httpx.ConnectError:
        logger.error("Ollama no disponible. ¿Está corriendo? → ollama serve")
        return None
    except Exception as e:
        logger.error(f"Error generando embedding: {e}")
        return None


async def generar_embeddings_batch(textos: list[str]) -> list[list[float] | None]:
    """
    Genera embeddings para múltiples textos en una sola llamada HTTP.
    Usa el endpoint /api/embed de Ollama (soportado desde v0.1.31).

    Args:
        textos: Lista de textos a embebir

    Returns:
        Lista de embeddings en el mismo orden. None para los que fallaron.
    """
    if not textos:
        return []

    try:
        response = await _get_client().post(
            f"{OLLAMA_URL}/api/embed",
            json={"model": EMBED_MODEL, "input": textos},
        )
        response.raise_for_status()
        embeddings = response.json().get("embeddings", [])

        if len(embeddings) != len(textos):
            logger.warning(
                f"Batch embedding: esperaba {len(textos)}, recibió {len(embeddings)}. "
                "Cayendo a modo individual."
            )
            return await _batch_individual(textos)

        return embeddings

    except httpx.ConnectError:
        logger.error("Ollama no disponible. ¿Está corriendo? → ollama serve")
        return [None] * len(textos)
    except Exception as e:
        logger.warning(f"Error en batch embedding ({e}) — cayendo a modo individual")
        return await _batch_individual(textos)


async def _batch_individual(textos: list[str]) -> list[list[float] | None]:
    """Fallback: genera embeddings uno a uno si el batch falla."""
    return [await generar_embedding(t) for t in textos]


# ── Helpers para compatibilidad con código existente ─────────────────────────

def vec_a_pg(vec: list[float]) -> str:
    """Convierte lista de floats al formato string pgvector: '[x,y,z]'."""
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"


async def generar_embedding_pg(texto: str) -> str | None:
    """
    Genera embedding y lo retorna como string para PostgreSQL + pgvector.
    Mantenido para compatibilidad — preferir generar_embedding() directamente.
    """
    vec = await generar_embedding(texto)
    if vec is None:
        return None
    return vec_a_pg(vec)
