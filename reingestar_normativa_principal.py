#!/usr/bin/env python3
"""
Re-ingestión urgente de normativa principal.

Elimina los documentos obsoletos (LOSNCP reforma + reglamentos viejos)
e ingesta los documentos vigentes:
  - LEY ORGÁNICA DEL SISTEMA NACIONAL DE CONTRATACIÓN PÚBLICA (vigente)
  - REGLAMENTO VIGENTE

Uso:
    python reingestar_normativa_principal.py [--dry-run]
"""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

from agent.memory import inicializar_db, eliminar_documento
from agent.ingestion import ingestar_archivo

# ── Documentos a ELIMINAR ────────────────────────────────────────────────────
# Palabras clave para identificar docs obsoletos (se normaliza a minúsculas sin guiones)
ELIMINAR_SLUGS = [
    "losncp ro 140",          # LOSNCP reforma (era solo la ley reformatoria, no la ley completa)
    "normativa secundaria",   # Normativa secundaria (contiene menor cuantía obsoleta)
    "reglamento general losncp",  # Reglamento anterior
    "reglamento losncp 2025", # Variante del nombre del reglamento anterior
]

# ── Documentos a INGESTAR (vigentes) ─────────────────────────────────────────
INGESTAR = [
    {
        "ruta":   "knowledge/LEY_ORGÁNICA_DEL_SISTEMA_NACIONAL_DE_CON (1).pdf",
        "tipo":   "ley",
        "nombre": "LEY_ORGANICA_SNCP_VIGENTE",
    },
    {
        "ruta":   "knowledge/1. REGLAMENTO VIGENTE.pdf",
        "tipo":   "reglamento",
        "nombre": "REGLAMENTO_LOSNCP_VIGENTE",
    },
]


async def listar_documentos_bd():
    """Lista todos los documentos en la BD con su id y nombre."""
    from sqlalchemy import select
    from agent.memory import async_session, Documento
    async with async_session() as session:
        result = await session.execute(
            select(Documento.id, Documento.nombre, Documento.tipo)
            .order_by(Documento.id)
        )
        return result.fetchall()


async def main(dry_run: bool = False):
    print("=" * 65)
    print("  RE-INGESTIÓN NORMATIVA PRINCIPAL")
    print("=" * 65)
    if dry_run:
        print("  [DRY RUN — no se modifica nada]")
    print()

    await inicializar_db()

    # ── Paso 1: listar documentos actuales ───────────────────────────────────
    docs = await listar_documentos_bd()
    print(f"Documentos en BD: {len(docs)}")
    for doc_id, nombre, tipo in docs:
        print(f"  [{doc_id:3d}] [{tipo:12s}] {nombre}")

    # ── Paso 2: eliminar documentos obsoletos ─────────────────────────────────
    print(f"\n── PASO 1: Eliminar documentos obsoletos ──────────────────────")
    eliminados = 0
    for doc_id, nombre, tipo in docs:
        # Coincidencia flexible: el nombre en BD puede ser el stem del archivo
        nombre_norm = nombre.lower().replace("-", " ").replace("_", " ")
        match = any(slug in nombre_norm for slug in ELIMINAR_SLUGS)
        if match:
            print(f"  🗑  [{doc_id}] {nombre}")
            if not dry_run:
                await eliminar_documento(doc_id)
                print(f"       → eliminado")
            else:
                print(f"       → [dry-run] se eliminaría")
            eliminados += 1

    if eliminados == 0:
        print("  (ningún documento coincide con la lista de eliminación)")

    # ── Paso 3: ingestar documentos vigentes ─────────────────────────────────
    print(f"\n── PASO 2: Ingestar documentos vigentes ───────────────────────")
    total_chunks = 0
    for doc in INGESTAR:
        ruta = Path(doc["ruta"])
        if not ruta.exists():
            print(f"  ❌ ARCHIVO NO ENCONTRADO: {ruta}")
            print(f"     Verifica que el archivo esté en knowledge/")
            continue

        size_mb = ruta.stat().st_size / 1_048_576
        print(f"\n  📄 {doc['nombre']}  ({size_mb:.1f} MB, tipo={doc['tipo']})")
        print(f"     {ruta}")

        if dry_run:
            print(f"     → [dry-run] se ingesta")
            continue

        resultado = await ingestar_archivo(
            str(ruta),
            tipo=doc["tipo"],
            nombre=doc["nombre"],
        )

        status = resultado.get("status")
        chunks = resultado.get("chunks", 0)

        if status == "ok":
            print(f"     ✅ {chunks} chunks indexados")
            total_chunks += chunks
        elif status == "ya_existia":
            print(f"     ⏭  ya existía ({chunks} chunks) — si quieres forzar elimina primero el doc de BD")
            total_chunks += chunks
        else:
            print(f"     ❌ ERROR: {resultado.get('detalle', 'desconocido')}")

    # ── Resumen ───────────────────────────────────────────────────────────────
    print(f"\n{'=' * 65}")
    if not dry_run:
        print(f"  Eliminados: {eliminados} documentos")
        print(f"  Indexados:  {total_chunks} chunks nuevos")
    else:
        print(f"  [dry-run] Se eliminarían {eliminados} docs e ingesta {len(INGESTAR)} nuevos")
    print(f"{'=' * 65}\n")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    asyncio.run(main(dry_run=dry_run))
