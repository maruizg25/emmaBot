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

import os
import json
import hashlib
from datetime import datetime
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
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    hash_contenido: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
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
    seccion: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pagina: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    # Vector nativo — evita casting ::vector en cada query
    embedding: Mapped[list[float] | None] = mapped_column(_VectorType, nullable=True)


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
            }
            for row in result.fetchall()
        ]


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
            }
            for row in result.fetchall()
        ]
