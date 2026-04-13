#!/usr/bin/env python3
"""
Migración: embedding_data (Text) → embedding (vector(768))

Qué hace:
  1. Añade columna `embedding vector(768)` a la tabla chunks
  2. Copia los datos de `embedding_data` con cast a vector
  3. Elimina la columna `embedding_data` (ya obsoleta)
  4. Recrea el índice HNSW en la nueva columna

Ejecutar UNA VEZ antes de arrancar el servidor con la nueva versión:
  python migrate_vector_column.py

Requiere DATABASE_URL en .env apuntando a PostgreSQL.
"""

import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL or "postgresql" not in DATABASE_URL:
    print("ERROR: Esta migración solo aplica a PostgreSQL.")
    print(f"DATABASE_URL actual: {DATABASE_URL!r}")
    sys.exit(1)

if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)


async def migrar():
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text

    engine = create_async_engine(DATABASE_URL, echo=False)

    async with engine.begin() as conn:
        # 1. Verificar si embedding_data existe
        result = await conn.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'chunks' AND column_name = 'embedding_data'
        """))
        tiene_embedding_data = result.fetchone() is not None

        # 2. Verificar si embedding (vector) ya existe
        result = await conn.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'chunks' AND column_name = 'embedding'
        """))
        tiene_embedding = result.fetchone() is not None

        if not tiene_embedding_data and tiene_embedding:
            print("✓ Migración ya realizada. No se requiere acción.")
            return

        if not tiene_embedding_data and not tiene_embedding:
            print("No se encontró ninguna columna de embedding. Creando `embedding`...")
            await conn.execute(text(
                "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding vector(768)"
            ))
            print("✓ Columna `embedding` creada (vacía).")
            return

        # 3. Asegurar extensión vector
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        print("✓ Extensión pgvector habilitada")

        # 4. Añadir columna embedding si no existe
        if not tiene_embedding:
            print("Añadiendo columna `embedding vector(768)`...")
            await conn.execute(text(
                "ALTER TABLE chunks ADD COLUMN embedding vector(768)"
            ))
            print("✓ Columna `embedding` añadida")

        # 5. Copiar datos
        result = await conn.execute(text(
            "SELECT COUNT(*) FROM chunks WHERE embedding_data IS NOT NULL AND embedding IS NULL"
        ))
        pendientes = result.scalar()
        print(f"Copiando {pendientes} embeddings de Text → vector(768)...")

        await conn.execute(text("""
            UPDATE chunks
            SET embedding = embedding_data::vector(768)
            WHERE embedding_data IS NOT NULL AND embedding IS NULL
        """))
        print("✓ Datos copiados")

        # 6. Eliminar columna antigua
        print("Eliminando columna `embedding_data`...")
        await conn.execute(text("ALTER TABLE chunks DROP COLUMN IF EXISTS embedding_data"))
        print("✓ Columna `embedding_data` eliminada")

        # 7. Eliminar índice viejo si existe
        await conn.execute(text(
            "DROP INDEX IF EXISTS idx_chunks_embedding_hnsw"
        ))

        # 8. Crear índice HNSW en la nueva columna
        print("Creando índice HNSW en `embedding`...")
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
            ON chunks USING hnsw (embedding vector_cosine_ops)
        """))
        print("✓ Índice HNSW creado")

    await engine.dispose()

    print()
    print("═══════════════════════════════════════")
    print("  Migración completada exitosamente.")
    print("  Ahora puedes arrancar el servidor.")
    print("═══════════════════════════════════════")


if __name__ == "__main__":
    asyncio.run(migrar())
