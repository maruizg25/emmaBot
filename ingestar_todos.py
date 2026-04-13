#!/usr/bin/env python3
"""
Script de ingestión masiva — corre directamente sin depender del servidor HTTP.
Uso: python ingestar_todos.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from agent.memory import inicializar_db
from agent.ingestion import ingestar_archivo

DIRECTORIOS = [
    ("knowledge", "ley"),
    ("knowledge/resoluciones", "resolucion"),
]

async def main():
    await inicializar_db()
    print("\n=== Ingestión masiva de PDFs ===\n")

    total_docs = 0
    total_chunks = 0

    for directorio, tipo in DIRECTORIOS:
        path = Path(directorio)
        if not path.exists():
            print(f"[SKIP] {directorio} no existe")
            continue

        pdfs = sorted(path.glob("*.pdf"))
        print(f"[{directorio}] {len(pdfs)} PDFs encontrados (tipo: {tipo})")

        for pdf in pdfs:
            print(f"  → {pdf.name} ...", end="", flush=True)
            result = await ingestar_archivo(str(pdf), tipo=tipo)
            status = result.get("status", "?")
            chunks = result.get("chunks", result.get("chunks_generados", 0))
            if status == "ya_existia":
                print(f" ya ingestado ({chunks} chunks)")
            elif status == "ok":
                print(f" OK — {chunks} chunks")
                total_docs += 1
                total_chunks += chunks
            else:
                print(f" ERROR: {result.get('detalle', result)}")

    print(f"\n=== Listo: {total_docs} documentos nuevos, {total_chunks} chunks totales ===\n")

if __name__ == "__main__":
    asyncio.run(main())
