# agent/rag/reranker.py — Reranking de chunks con cross-encoder

"""
Reranker de dos etapas para el RAG normativo SERCOP.

Pipeline:
  RRF(semántico + full-text) → top-12 candidatos
  Cross-encoder mMiniLM       → rerank → top-4 relevantes

Modelo: cross-encoder/mmarco-mMiniLMv2-L12-H384-v1
  - Multilingüe (incluye español)
  - ~100 MB, carga en CPU en <5s
  - Latencia: ~0.5s para 12 pares en CPU

Si sentence-transformers no está instalado, devuelve los chunks
en el mismo orden (RRF ya es razonablemente bueno como fallback).

Variables de entorno:
  RERANKER_ENABLED   true|false  Activar/desactivar reranker (default: true)
  HF_HOME            ruta        Directorio de caché de modelos HuggingFace
                                 (default: <raíz_proyecto>/models/hf_cache)
"""

import os
import logging

logger = logging.getLogger("agentkit")

# ── Configuración por variables de entorno ────────────────────────────────────

RERANKER_ENABLED = os.getenv("RERANKER_ENABLED", "true").lower() == "true"

# Directorio de caché para modelos HuggingFace.
# Si HF_HOME no está en el entorno, apunta a models/hf_cache dentro del proyecto
# para que el modelo se descargue una vez y quede en disco.
_HF_HOME_DEFAULT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "models", "hf_cache")
)
_HF_HOME = os.getenv("HF_HOME", _HF_HOME_DEFAULT)
os.environ.setdefault("HF_HOME", _HF_HOME)
os.environ.setdefault("TRANSFORMERS_CACHE", _HF_HOME)

# ── Estado del módulo ─────────────────────────────────────────────────────────

# Lazy load — el modelo se carga en primer uso, no al importar el módulo
_reranker = None
_reranker_intentado = False

# Modelo multilingüe ligero, óptimo para texto legal en español
RERANKER_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
TOP_N_RERANK = 4


def _get_reranker():
    """Carga el cross-encoder la primera vez, cachea para requests siguientes."""
    global _reranker, _reranker_intentado
    if _reranker_intentado:
        return _reranker

    _reranker_intentado = True

    if not RERANKER_ENABLED:
        logger.info("Reranker desactivado (RERANKER_ENABLED=false) — usando orden RRF")
        return None

    logger.info(f"Cargando reranker desde caché: {_HF_HOME}")
    try:
        from sentence_transformers import CrossEncoder
        _reranker = CrossEncoder(RERANKER_MODEL)
        logger.info(f"Reranker listo: {RERANKER_MODEL}")
    except ImportError:
        logger.warning(
            "sentence-transformers no instalado — reranking desactivado. "
            "Instalar: pip install sentence-transformers"
        )
        _reranker = None
    except Exception as e:
        logger.warning(f"Error cargando reranker ({e}) — usando orden RRF")
        _reranker = None

    return _reranker


def rerank(query: str, chunks: list[dict], top_n: int = TOP_N_RERANK) -> list[dict]:
    """
    Re-ordena chunks por relevancia semántica real usando cross-encoder.

    Args:
        query: Pregunta original del usuario
        chunks: Lista de chunks recuperados por RRF (ya están pre-filtrados)
        top_n: Cuántos chunks retornar después del reranking

    Returns:
        Subconjunto de chunks ordenados por score cross-encoder descendente.
        Si el reranker no está disponible, retorna chunks[:top_n] sin cambios.
    """
    if not chunks:
        return chunks

    reranker = _get_reranker()
    if reranker is None:
        return chunks[:top_n]

    try:
        pares = [(query, chunk["texto"]) for chunk in chunks]
        scores = reranker.predict(pares)

        ranked = sorted(
            zip(scores, chunks),
            key=lambda x: float(x[0]),
            reverse=True,
        )
        resultado = [chunk for _, chunk in ranked[:top_n]]

        top_score = float(ranked[0][0]) if ranked else 0.0
        logger.info(
            f"Reranker: {len(chunks)} candidatos → {len(resultado)} seleccionados "
            f"(top score: {top_score:.3f})"
        )
        return resultado

    except Exception as e:
        logger.warning(f"Error en reranking: {e} — usando orden RRF original")
        return chunks[:top_n]
