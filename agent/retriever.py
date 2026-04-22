# agent/retriever.py — Búsqueda híbrida semántica + full-text + reranking

"""
Motor de búsqueda RAG para el agente SERCOP.

Pipeline:
  1. Query expansion — expande siglas y sinónimos del dominio legal ecuatoriano
  2. Búsqueda híbrida — semántica (pgvector coseno) + full-text (tsvector español)
  3. RRF — fusiona los dos rankings en uno con Reciprocal Rank Fusion
  4. Reranking — cross-encoder re-ordena los candidatos por relevancia real

Mejoras vs versión anterior:
- Query expansion para siglas SERCOP (RUP, PAC, SIE, LOSNCP, etc.)
- Reranker cross-encoder: top-12 RRF → top-4 final
- TOP_K_BUSQUEDA subido a 12 (más candidatos para el reranker)
"""

import os
import logging
from agent.embeddings import generar_embedding
from agent.memory import buscar_chunks_semantico, buscar_chunks_fulltext, _is_postgres

logger = logging.getLogger("agentkit")

RRF_K           = 60   # Constante estándar de investigación
TOP_K_BUSQUEDA  = 12   # Candidatos por búsqueda antes de fusionar
TOP_K_RERANK    = int(os.getenv("TOP_K_CHUNKS", "4"))  # Chunks finales al prompt (4 = mejor diversidad)

# Similitud coseno mínima para aceptar un chunk semántico.
# 0.50 validado empíricamente — 0.55 descartaba chunks relevantes en temas específicos.
# (Configurable vía RAG_SCORE_MINIMO; solo aplica a chunks semánticos)
RAG_SCORE_MINIMO = float(os.getenv("RAG_SCORE_MINIMO", "0.50"))

# ── Diccionario de expansión de queries ──────────────────────────────────────
# Siglas y términos técnicos del dominio de contratación pública ecuatoriana
# Clave: término corto/sigla que puede aparecer en la pregunta del usuario
# Valor: expansión que mejora el recall en la búsqueda

EXPANSIONES_LEGALES: dict[str, str] = {
    "rup":              "Registro Único de Proveedores RUP inscripción habilitación",
    "pac":              "Plan Anual de Contratación PAC planificación",
    "sie":              "Subasta Inversa Electrónica SIE procedimiento",
    "losncp":           "Ley Orgánica Sistema Nacional Contratación Pública LOSNCP",
    "rglosncp":         "Reglamento General Ley Orgánica Sistema Nacional Contratación Pública",
    "sercop":           "Servicio Nacional Contratación Pública SERCOP",
    "sncp":             "Sistema Nacional Contratación Pública SNCP",
    "ínfima cuantía":   "ínfima cuantía contratación directa monto límite",
    "infima cuantia":   "ínfima cuantía contratación directa monto límite",
    "menor cuantía":    "menor cuantía bienes servicios obras procedimiento",
    "cotización":       "cotización procedimiento bienes servicios obras",
    "licitación":       "licitación procedimiento concurso público ofertas",
    "catálogo":         "catálogo electrónico portal compraspúblicas convenio marco",
    "catalogo":         "catálogo electrónico portal compraspúblicas convenio marco",
    "ferias inclusivas": "ferias inclusivas economía popular solidaria EPS MIPYMES",
    "régimen especial": "régimen especial contratación directa excepción",
    "regimen especial": "régimen especial contratación directa excepción",
    "consultoría":      "consultoría servicios profesionales especializados",
    "consultoria":      "consultoría servicios profesionales especializados",
    "garantía":         "garantía fiel cumplimiento buen uso anticipo calidad",
    "garantia":         "garantía fiel cumplimiento buen uso anticipo calidad",
    "proveedor":        "proveedor oferente RUP registro único contratista",
    "entidad":          "entidad contratante pública institución del estado",
    "pliegos":          "pliegos documentos precontractuales bases concurso",
    "oferta":           "oferta propuesta técnica económica calificación",
    "adjudicación":     "adjudicación resolución contrato award",
    "adjudicacion":     "adjudicación resolución contrato award",
}


def _expandir_query(query: str) -> str:
    """
    Expande la query del usuario con sinónimos del dominio legal.

    Detecta siglas y términos técnicos en la query y añade sus expansiones
    al final. Esto mejora el recall en la búsqueda full-text y semántica.

    Ejemplo:
        "¿Cómo me registro en el RUP?"
        → "¿Cómo me registro en el RUP? Registro Único de Proveedores RUP inscripción habilitación"
    """
    query_lower = query.lower()
    expansiones = []

    for termino, expansion in EXPANSIONES_LEGALES.items():
        if termino in query_lower and expansion not in query_lower:
            expansiones.append(expansion)

    if expansiones:
        query_expandida = query + " " + " ".join(expansiones)
        logger.debug(f"Query expandida: {query_expandida[:120]}")
        return query_expandida

    return query


def _rrf(
    lista_semantica: list[dict],
    lista_fulltext: list[dict],
    k: int = RRF_K,
) -> list[dict]:
    """
    Reciprocal Rank Fusion: combina dos rankings en uno solo.
    Fórmula: score(d) = Σ 1/(k + rank_i(d))
    """
    scores: dict[int, float] = {}
    chunks_por_id: dict[int, dict] = {}

    for rank, chunk in enumerate(lista_semantica, start=1):
        cid = chunk["id"]
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
        chunks_por_id[cid] = chunk

    for rank, chunk in enumerate(lista_fulltext, start=1):
        cid = chunk["id"]
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
        chunks_por_id.setdefault(cid, chunk)

    ordenados = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [chunks_por_id[cid] for cid, _ in ordenados]


async def buscar_contexto(query: str) -> list[dict]:
    """
    Pipeline completo: expand → híbrido → RRF → rerank.

    Returns:
        Lista final de chunks más relevantes (≤ TOP_K_RERANK).
    """
    query_expandida = _expandir_query(query)

    if _is_postgres:
        # Búsqueda semántica
        embedding = await generar_embedding(query_expandida)
        if embedding:
            semanticos = await buscar_chunks_semantico(embedding, TOP_K_BUSQUEDA)
        else:
            logger.warning("Embedding no disponible, usando solo full-text")
            semanticos = []

        # Búsqueda full-text
        fulltext = await buscar_chunks_fulltext(query_expandida, TOP_K_BUSQUEDA)

        # Fusionar con RRF
        candidatos = _rrf(semanticos, fulltext)[:TOP_K_BUSQUEDA]
    else:
        # SQLite dev — solo full-text, sin reranking
        candidatos = await buscar_chunks_fulltext(query, TOP_K_RERANK)
        return candidatos[:TOP_K_RERANK]

    if not candidatos:
        return []

    # Reranking con cross-encoder (query original, sin expansión)
    from agent.rag.reranker import rerank
    return rerank(query, candidatos, top_n=TOP_K_RERANK)


def formatear_contexto(chunks: list[dict]) -> str:
    """Formatea los chunks como contexto para inyectar en el prompt."""
    if not chunks:
        return ""

    # Mapa de nombre de documento a nombre oficial citable
    _NOMBRE_OFICIAL = {
        "ley":        "LOSNCP",
        "reglamento": "RGLOSNCP",
    }

    bloques = []
    for i, chunk in enumerate(chunks, start=1):
        meta = chunk.get("metadata", {})
        tipo = meta.get("tipo", "")
        seccion = chunk.get("seccion") or ""
        pagina = chunk.get("pagina")

        # Nombre citable: para ley/reglamento usar sigla oficial; para resoluciones
        # intentar extraer el código RE-SERCOP-XXXX-NNNN del nombre del archivo
        nombre_doc = meta.get("nombre_doc", "Documento SERCOP")
        if tipo in _NOMBRE_OFICIAL:
            doc_citable = _NOMBRE_OFICIAL[tipo]
        else:
            import re as _re
            m = _re.search(r"R\.?E\.?[-\s]?SERCOP[-\s]?\d{4}[-\s]\d{4}", nombre_doc, _re.IGNORECASE)
            doc_citable = m.group(0).upper() if m else nombre_doc

        # Artículo: primero del metadata enriquecido, luego de la sección
        articulo_meta = meta.get("articulo")
        articulo_label = ""
        if articulo_meta:
            articulo_label = f"{articulo_meta}"
        elif seccion:
            articulo_label = seccion

        # Encabezado: "LOSNCP | Art. 74" o "RGLOSNCP | Art. 228"
        encabezado_partes = [doc_citable]
        if articulo_label:
            encabezado_partes.append(f"| {articulo_label}")
        if pagina:
            encabezado_partes.append(f"[p. {pagina}]")

        bloques.append(f"[FUENTE {i}: {' '.join(encabezado_partes)}]\n{chunk['texto']}")

    return "\n\n---\n\n".join(bloques)


async def recuperar_contexto_formateado(query: str) -> tuple[str, int]:
    """
    Punto de entrada principal del retriever.

    Returns:
        (contexto_formateado, num_chunks_encontrados)
    """
    chunks = await buscar_contexto(query)
    if not chunks:
        logger.info(f"RAG: sin resultados para '{query[:60]}'")
        return "", 0

    # ── Filtro de relevancia semántica ────────────────────────────────────────
    # Descartar chunks semánticos con score < RAG_SCORE_MINIMO para evitar
    # que el LLM responda sobre temas incorrectos por chunks de baja similitud.
    # Los chunks fulltext (ts_rank) tienen escala diferente y no se filtran aquí.
    sem_chunks = [c for c in chunks if c.get("source") == "semantic"]
    if sem_chunks:
        top_score = max(c["score"] for c in sem_chunks)
        if top_score < RAG_SCORE_MINIMO:
            logger.info(
                f"RAG: descartado — top score semántico {top_score:.3f} < {RAG_SCORE_MINIMO} "
                f"para '{query[:60]}'"
            )
            return "", 0

    contexto = formatear_contexto(chunks)
    logger.info(f"RAG: {len(chunks)} chunks finales (top semántico: {sem_chunks[0]['score']:.3f}) para '{query[:60]}'")
    return contexto, len(chunks)
