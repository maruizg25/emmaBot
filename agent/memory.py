# agent/memory.py — Memoria de conversaciones + base de conocimiento SERCOP

"""
Sistema de persistencia del agente SERCOP:
- Historial de conversaciones por número de teléfono
- Repositorio de documentos y chunks vectorizados para RAG

Mejoras vs versión anterior:
- embedding almacenado como vector(768) nativo (no Text)
- Queries parametrizadas — sin interpolación de strings en SQL
- Búsqueda vectorial usa bindparam para el vector de query
"""

from __future__ import annotations

import os
import json
import hashlib
from datetime import datetime
from typing import Optional, List
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import (
    String, Text, DateTime, Integer, ForeignKey,
    select, delete, func, text
)
from dotenv import load_dotenv

load_dotenv()


def _build_database_url() -> str:
    url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./agentkit.db")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


DATABASE_URL = _build_database_url()
_is_postgres = DATABASE_URL.startswith("postgresql")

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=10 if _is_postgres else 1,
    max_overflow=20 if _is_postgres else 0,
    pool_pre_ping=True,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# ── Tipo Vector para pgvector ─────────────────────────────────────────────────

if _is_postgres:
    try:
        from pgvector.sqlalchemy import Vector as _PGVector
        _VectorType = _PGVector(768)
    except ImportError:
        # pgvector no instalado — usar Text como fallback
        _VectorType = Text
        _is_postgres = False
else:
    _VectorType = Text


class Base(DeclarativeBase):
    pass


# ─── Modelos ─────────────────────────────────────────────────────────────────

class Mensaje(Base):
    """Historial de conversaciones por teléfono."""
    __tablename__ = "mensajes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(50), index=True)
    role: Mapped[str] = mapped_column(String(20))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Documento(Base):
    """Metadatos de cada documento ingestado en la base de conocimiento."""
    __tablename__ = "documentos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(500))
    tipo: Mapped[str] = mapped_column(String(50), index=True)
    url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    hash_contenido: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True)
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    fecha_ingestion: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Chunk(Base):
    """
    Fragmento de texto con su embedding vectorial para búsqueda semántica (RAG).

    El embedding se almacena como vector(768) nativo en PostgreSQL usando pgvector.
    Esto permite búsqueda vectorial eficiente sin castings en runtime.
    """
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    documento_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("documentos.id", ondelete="CASCADE"), index=True
    )
    texto: Mapped[str] = mapped_column(Text)
    seccion: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    pagina: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    # Vector nativo — evita casting ::vector en cada query
    embedding: Mapped[Optional[List[float]]] = mapped_column(_VectorType, nullable=True)


# ─── Inicialización ──────────────────────────────────────────────────────────

async def inicializar_db():
    """Crea tablas, habilita pgvector y crea índices de búsqueda."""
    async with engine.begin() as conn:
        if _is_postgres:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS unaccent"))

        await conn.run_sync(Base.metadata.create_all)

        if _is_postgres:
            # Índice HNSW para búsqueda vectorial por coseno
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
                ON chunks USING hnsw (embedding vector_cosine_ops)
            """))
            # Índice GIN para búsqueda full-text en español
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_chunks_tsvector_es
                ON chunks USING gin(to_tsvector('spanish', texto))
            """))
            # Tabla de aprendizaje continuo
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS consultas_log (
                    id                BIGSERIAL PRIMARY KEY,
                    telefono_hash     VARCHAR(64),
                    pregunta          TEXT NOT NULL,
                    pregunta_normalizada TEXT,
                    respuesta         TEXT,
                    proveedor_llm     VARCHAR(20),
                    tiempo_ms         INTEGER,
                    fue_shortcut      BOOLEAN DEFAULT FALSE,
                    shortcut_tipo     VARCHAR(30),
                    rag_chunks        INTEGER DEFAULT 0,
                    timestamp         TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_consultas_timestamp
                ON consultas_log (timestamp DESC)
            """))
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_consultas_shortcut
                ON consultas_log (fue_shortcut, timestamp DESC)
            """))


# ─── Mensajes ────────────────────────────────────────────────────────────────

async def guardar_mensaje(telefono: str, role: str, content: str):
    async with async_session() as session:
        session.add(Mensaje(
            telefono=telefono, role=role, content=content, timestamp=datetime.utcnow()
        ))
        await session.commit()


async def obtener_historial(telefono: str, limite: int = 20) -> list[dict]:
    async with async_session() as session:
        result = await session.execute(
            select(Mensaje)
            .where(Mensaje.telefono == telefono)
            .order_by(Mensaje.timestamp.desc())
            .limit(limite)
        )
        mensajes = list(reversed(result.scalars().all()))
        return [{"role": m.role, "content": m.content} for m in mensajes]


async def limpiar_historial(telefono: str):
    async with async_session() as session:
        await session.execute(delete(Mensaje).where(Mensaje.telefono == telefono))
        await session.commit()


# ─── Documentos ──────────────────────────────────────────────────────────────

async def crear_documento(nombre: str, tipo: str, url: str | None, contenido: str) -> int:
    """Registra un documento. Si ya existe (mismo hash), retorna el ID existente."""
    hash_doc = hashlib.sha256(contenido.encode("utf-8")).hexdigest()
    async with async_session() as session:
        existing = await session.execute(
            select(Documento).where(Documento.hash_contenido == hash_doc)
        )
        doc = existing.scalar_one_or_none()
        if doc:
            return doc.id
        doc = Documento(nombre=nombre, tipo=tipo, url=url, hash_contenido=hash_doc)
        session.add(doc)
        await session.commit()
        await session.refresh(doc)
        return doc.id


async def actualizar_total_chunks(documento_id: int, total: int):
    async with async_session() as session:
        result = await session.execute(select(Documento).where(Documento.id == documento_id))
        doc = result.scalar_one_or_none()
        if doc:
            doc.total_chunks = total
            await session.commit()


async def listar_documentos() -> list[dict]:
    async with async_session() as session:
        result = await session.execute(
            select(Documento).order_by(Documento.fecha_ingestion.desc())
        )
        return [
            {
                "id": d.id,
                "nombre": d.nombre,
                "tipo": d.tipo,
                "url": d.url,
                "total_chunks": d.total_chunks,
                "fecha_ingestion": d.fecha_ingestion.isoformat(),
            }
            for d in result.scalars().all()
        ]


async def eliminar_documento(documento_id: int):
    async with async_session() as session:
        await session.execute(delete(Chunk).where(Chunk.documento_id == documento_id))
        await session.execute(delete(Documento).where(Documento.id == documento_id))
        await session.commit()


# ─── Chunks ──────────────────────────────────────────────────────────────────

async def guardar_chunk(
    documento_id: int,
    texto: str,
    embedding: list[float] | None,
    seccion: str | None,
    pagina: int | None,
    metadata: dict,
):
    async with async_session() as session:
        session.add(Chunk(
            documento_id=documento_id,
            texto=texto,
            embedding=embedding,
            seccion=seccion,
            pagina=pagina,
            metadata_json=json.dumps(metadata, ensure_ascii=False),
        ))
        await session.commit()


async def contar_chunks_documento(documento_id: int) -> int:
    async with async_session() as session:
        result = await session.execute(
            select(func.count()).select_from(Chunk).where(Chunk.documento_id == documento_id)
        )
        return result.scalar() or 0


async def buscar_chunks_semantico(query_embedding: list[float], top_k: int = 12) -> list[dict]:
    """
    Búsqueda vectorial por coseno usando pgvector.
    Usa query parametrizada — sin interpolación de strings.
    """
    if not _is_postgres or not query_embedding:
        return []

    # El vector se pasa como string en formato pgvector y se castea en SQL
    # Esto es seguro: el valor viene de nuestro modelo, no del usuario
    vec_str = "[" + ",".join(f"{v:.8f}" for v in query_embedding) + "]"

    sql = text("""
        SELECT c.id, c.texto, c.seccion, c.pagina, c.metadata_json,
               (c.embedding <=> (:q)::vector) AS distancia
        FROM chunks c
        WHERE c.embedding IS NOT NULL
        ORDER BY c.embedding <=> (:q)::vector
        LIMIT :top_k
    """)
    async with engine.connect() as conn:
        result = await conn.execute(sql, {"q": vec_str, "top_k": top_k})
        return [
            {
                "id": row.id,
                "texto": row.texto,
                "seccion": row.seccion,
                "pagina": row.pagina,
                "metadata": json.loads(row.metadata_json or "{}"),
                "score": 1.0 - float(row.distancia),
                "source": "semantic",
            }
            for row in result.fetchall()
        ]


async def registrar_consulta(
    pregunta: str,
    pregunta_normalizada: str,
    respuesta: str,
    proveedor_llm: str,
    tiempo_ms: int,
    fue_shortcut: bool,
    shortcut_tipo: str | None,
    rag_chunks: int,
    telefono: str = "",
) -> None:
    """Registra cada consulta en consultas_log para aprendizaje continuo."""
    if not _is_postgres:
        return  # Solo disponible con PostgreSQL
    telefono_hash = hashlib.sha256(telefono.encode()).hexdigest() if telefono else None
    async with async_session() as session:
        await session.execute(text("""
            INSERT INTO consultas_log
                (telefono_hash, pregunta, pregunta_normalizada, respuesta,
                 proveedor_llm, tiempo_ms, fue_shortcut, shortcut_tipo, rag_chunks)
            VALUES
                (:th, :p, :pn, :r, :llm, :ms, :sc, :st, :rc)
        """), {
            "th":  telefono_hash,
            "p":   pregunta[:1000],
            "pn":  pregunta_normalizada[:500],
            "r":   respuesta[:2000],
            "llm": proveedor_llm[:20],
            "ms":  tiempo_ms,
            "sc":  fue_shortcut,
            "st":  shortcut_tipo,
            "rc":  rag_chunks,
        })
        await session.commit()


async def buscar_articulo_directo(num_articulo: int, tipo_doc: str = "ley") -> str | None:
    """Busca el texto exacto de un artículo en los chunks de la BD."""
    if not _is_postgres:
        return None
    try:
        async with async_session() as session:
            result = await session.execute(text("""
                SELECT c.id, c.texto, c.seccion, d.nombre
                FROM chunks c
                JOIN documentos d ON c.documento_id = d.id
                WHERE d.tipo = :tipo
                  AND c.texto ILIKE :pattern
                ORDER BY c.id
                LIMIT 1
            """), {"tipo": tipo_doc, "pattern": f"%Art. {num_articulo}.-%"})
            row = result.fetchone()
            if not row:
                result = await session.execute(text("""
                    SELECT c.id, c.texto, c.seccion, d.nombre
                    FROM chunks c
                    JOIN documentos d ON c.documento_id = d.id
                    WHERE d.tipo = :tipo
                      AND c.texto ILIKE :pattern2
                    ORDER BY c.id
                    LIMIT 1
                """), {"tipo": tipo_doc, "pattern2": f"%Art. {num_articulo}%"})
                row = result.fetchone()
            if not row:
                return None
            texto = row.texto
            chunk_id = row.id
            idx = texto.find(f"Art. {num_articulo}.")
            if idx < 0:
                idx = texto.find(f"Art. {num_articulo}")
            if idx >= 0:
                texto = texto[idx:]
            next_art = num_articulo + 1
            if f"Art. {next_art}" not in texto and len(texto) < 400:
                result2 = await session.execute(text("""
                    SELECT c.texto FROM chunks c
                    WHERE c.id = :next_id
                """), {"next_id": chunk_id + 1})
                row2 = result2.fetchone()
                if row2:
                    next_text = row2.texto
                    next_idx = next_text.find(f"Art. {next_art}")
                    if next_idx > 0:
                        texto += "\n" + next_text[:next_idx]
                    elif next_idx < 0:
                        texto += "\n" + next_text
            for noise in ["https://edicioneslegales.com.ec/", "Todos los derechos reservados.",
                          "Prohibida su reproducción parcial o total.",
                          "Piense en el medio ambiente. Imprima solo de ser necesario."]:
                texto = texto.replace(noise, "")
            import re as _re
            texto = _re.sub(r'Pág\.\s*\d+\s*de\s*\d+', '', texto)
            texto = _re.sub(r'REGLAMENTO DE LA LEY ORGÁNICA.*?\n', '', texto)
            texto = _re.sub(r'Página\s*\d+\s*de\s*\d+', '', texto)
            texto = _re.sub(r'\n\s*\n', '\n', texto).strip()
            texto = _re.sub(r'  +', ' ', texto)
            max_chars = 1500
            truncado = len(texto) > max_chars
            texto = texto[:max_chars]
            fuente = "LOSNCP" if tipo_doc == "ley" else "RGLOSNCP"
            resultado = f"📜 *{fuente} — Art. {num_articulo}*\n\n{texto}"
            if truncado:
                resultado += "\n\n_(artículo truncado por extensión)_"
            resultado += f"\n\n📌 Fuente: {fuente} vigente"
            return resultado
    except Exception:
        return None


async def buscar_respuesta_cacheada(pregunta_normalizada: str) -> str | None:
    """Busca en consultas_log si una pregunta similar ya fue respondida por LLM."""
    if not _is_postgres:
        return None
    try:
        async with async_session() as session:
            result = await session.execute(text("""
                SELECT respuesta FROM consultas_log
                WHERE pregunta_normalizada = :pn
                  AND fue_shortcut = FALSE
                  AND proveedor_llm NOT IN ('none', 'shortcut', 'cache_llm', 'articulo_directo', 'tool_directo')
                  AND respuesta IS NOT NULL
                  AND length(respuesta) > 200
                ORDER BY timestamp DESC
                LIMIT 1
            """), {"pn": pregunta_normalizada[:500]})
            row = result.fetchone()
            return row.respuesta if row else None
    except Exception:
        return None


async def obtener_estadisticas() -> dict:
    """Estadísticas para el endpoint GET /admin/stats."""
    if not _is_postgres:
        return {"error": "estadísticas disponibles solo con PostgreSQL"}

    async with engine.connect() as conn:
        # Hoy
        hoy = await conn.execute(text("""
            SELECT
                COUNT(*)                                              AS total,
                SUM(CASE WHEN fue_shortcut     THEN 1 ELSE 0 END)    AS shortcuts,
                SUM(CASE WHEN NOT fue_shortcut THEN 1 ELSE 0 END)    AS rag_api,
                SUM(CASE WHEN shortcut_tipo = 'faq_cache' THEN 1 ELSE 0 END) AS cache_hits,
                AVG(tiempo_ms)                                        AS avg_ms,
                MODE() WITHIN GROUP (ORDER BY proveedor_llm)          AS proveedor_top
            FROM consultas_log
            WHERE timestamp >= CURRENT_DATE
        """))
        fila_hoy = hoy.fetchone()

        # Semana
        semana = await conn.execute(text("""
            SELECT
                COUNT(*)                                              AS total,
                SUM(CASE WHEN fue_shortcut     THEN 1 ELSE 0 END)    AS shortcuts,
                SUM(CASE WHEN NOT fue_shortcut THEN 1 ELSE 0 END)    AS rag_api,
                SUM(CASE WHEN shortcut_tipo = 'faq_cache' THEN 1 ELSE 0 END) AS cache_hits,
                AVG(tiempo_ms)                                        AS avg_ms
            FROM consultas_log
            WHERE timestamp >= NOW() - INTERVAL '7 days'
        """))
        fila_sem = semana.fetchone()

        # Top preguntas (última semana, no shortcuts)
        top_q = await conn.execute(text("""
            SELECT pregunta_normalizada, COUNT(*) AS frecuencia
            FROM consultas_log
            WHERE timestamp >= NOW() - INTERVAL '7 days'
              AND NOT fue_shortcut
            GROUP BY pregunta_normalizada
            ORDER BY frecuencia DESC
            LIMIT 10
        """))
        top_preguntas = [{"pregunta": r.pregunta_normalizada, "frecuencia": r.frecuencia}
                         for r in top_q.fetchall()]

        # Desglose por proveedor (hoy)
        prov = await conn.execute(text("""
            SELECT proveedor_llm, COUNT(*) AS total
            FROM consultas_log
            WHERE timestamp >= CURRENT_DATE
            GROUP BY proveedor_llm
        """))
        proveedores = {r.proveedor_llm: r.total for r in prov.fetchall()}

    def _porcentaje_ahorro(sh, total):
        if not total:
            return "0%"
        return f"{int(sh / total * 100)}%"

    total_hoy  = int(fila_hoy.total or 0)
    sh_hoy     = int(fila_hoy.shortcuts or 0)
    ch_hoy     = int(fila_hoy.cache_hits or 0)

    total_sem  = int(fila_sem.total or 0)
    sh_sem     = int(fila_sem.shortcuts or 0)

    return {
        "hoy": {
            "total_consultas":      total_hoy,
            "shortcuts":            sh_hoy,
            "rag_api":              int(fila_hoy.rag_api or 0),
            "porcentaje_ahorro":    _porcentaje_ahorro(sh_hoy, total_hoy),
            "proveedor_mas_usado":  fila_hoy.proveedor_top or "ninguno",
            "tiempo_promedio_ms":   int(fila_hoy.avg_ms or 0),
            "cache_hits":           ch_hoy,
        },
        "semana": {
            "total_consultas":      total_sem,
            "shortcuts":            sh_sem,
            "rag_api":              int(fila_sem.rag_api or 0),
            "porcentaje_ahorro":    _porcentaje_ahorro(sh_sem, total_sem),
            "tiempo_promedio_ms":   int(fila_sem.avg_ms or 0),
            "cache_hits":           int(fila_sem.cache_hits or 0),
        },
        "top_preguntas": top_preguntas,
        "proveedores":   proveedores,
    }


async def buscar_chunks_fulltext(query: str, top_k: int = 12) -> list[dict]:
    """Búsqueda full-text en español usando tsvector. Fallback LIKE en SQLite."""
    async with async_session() as session:
        if _is_postgres:
            result = await session.execute(
                text("""
                    SELECT c.id, c.texto, c.seccion, c.pagina, c.metadata_json,
                           ts_rank(to_tsvector('spanish', unaccent(c.texto)),
                                   plainto_tsquery('spanish', unaccent(:q))) AS rank
                    FROM chunks c
                    WHERE to_tsvector('spanish', unaccent(c.texto)) @@
                          plainto_tsquery('spanish', unaccent(:q))
                    ORDER BY rank DESC
                    LIMIT :top_k
                """),
                {"q": query, "top_k": top_k},
            )
        else:
            result = await session.execute(
                text("""
                    SELECT id, texto, seccion, pagina, metadata_json, 1.0 AS rank
                    FROM chunks
                    WHERE LOWER(texto) LIKE :pattern
                    LIMIT :top_k
                """),
                {"pattern": f"%{query.lower()}%", "top_k": top_k},
            )
        return [
            {
                "id": row.id,
                "texto": row.texto,
                "seccion": row.seccion,
                "pagina": row.pagina,
                "metadata": json.loads(row.metadata_json or "{}"),
                "score": float(row.rank),
                "source": "fulltext",
            }
            for row in result.fetchall()
        ]
