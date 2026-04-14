#!/usr/bin/env python3
"""Script de ingestión con progreso por documento."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from agent.memory import inicializar_db
from agent.ingestion import ingestar_archivo

ARCHIVOS = [
    # ── Normativa principal ───────────────────────────────────────────────
    ("knowledge/LOSNCP-RO-140-07-X-2025-1.pdf",                                                       "ley"),
    ("knowledge/ANEXO-1-GLOSARIO-DE-TERMINOS.pdf",                                                     "manual"),
    ("knowledge/2.-Resolución-Nro.-R.E-SERCOP-2026-0002-Expide-Norma-Interna-de-Funcionamiento-del-Comite-Interinstitucional.pdf", "resolucion"),

    # ── Biblioteca SERCOP (reglamento + manuales SOCE) ───────────────────
    ("knowledge/biblioteca/Reglamento-LOSNCP-20251030.pdf",                                            "reglamento"),
    ("knowledge/biblioteca/normativa-secundaria-actualizada-1.pdf",                                    "reglamento"),
    ("knowledge/biblioteca/Manual-SOCE-Subasta-inversa-Electronica-bienes-o-servicios-Entidades-Contratantes.pdf", "manual_soce"),
    ("knowledge/biblioteca/Manual-fase-contractual-bienes-y-servicios-SOCE.pdf",                       "manual_soce"),
    ("knowledge/biblioteca/Acuerdo-012-2019.pdf",                                                      "resolucion"),
    ("knowledge/biblioteca/1.-RESOLUCIÓN-R.E-SERCOP-2026-0001-METODOLOGÍA-CONTROL.pdf",               "resolucion"),
    ("knowledge/biblioteca/1.1-METODOLOGÍA-DE-CONTROL-FINAL-SUMILLADA-1.pdf",                         "resolucion"),
    ("knowledge/biblioteca/2.-Resolución-Nro.-R.E-SERCOP-2026-0002-Expide-Norma-Interna-de-Funcionamiento-del-Comite-Interinstitucional.pdf", "resolucion"),
    ("knowledge/biblioteca/2.1-Norma-Interna-de-Funcionamiento-de-la-Subasta-Inversa-Corporativa-de-fármacos-y-biens-estregicos-en-salud-Anexo-Res.pdf", "resolucion"),
    ("knowledge/biblioteca/R.E-SERCOP-2025-0152.pdf",                                                 "resolucion"),
    ("knowledge/biblioteca/Resolucion-cod-etica-23-3-26.pdf",                                         "resolucion"),
    ("knowledge/biblioteca/Resolución_régimen_de_transición_20-11-2025.pdf",                          "resolucion"),

    # ── Resoluciones (carpeta resoluciones/) ─────────────────────────────
    ("knowledge/resoluciones/1.-RESOLUCIÓN-R.E-SERCOP-2026-0001-METODOLOGÍA-CONTROL.pdf",             "resolucion"),
    ("knowledge/resoluciones/1.1-METODOLOGÍA-DE-CONTROL-FINAL-SUMILLADA-1.pdf",                       "resolucion"),
    ("knowledge/resoluciones/2.1-Norma-Interna-de-Funcionamiento-de-la-Subasta-Inversa-Corporativa-de-fármacos-y-biens-estregicos-en-salud-Anexo-Resolución-R.E-SERCOP-2026-0002-1.pdf", "resolucion"),
    ("knowledge/resoluciones/FE-DE-ERRATA-RESOLUCION-No.-RE-SERCOP-2024-0142.pdf",                    "resolucion"),
    ("knowledge/resoluciones/R.E-SERCOP-2025-0152-signed-signed-2.pdf",                               "resolucion"),
    ("knowledge/resoluciones/RESOLUCION-Nro.-RE-SERCOP-2024-0144.pdf",                                "resolucion"),
    ("knowledge/resoluciones/Resolucion-cod-etica-23-3-26-signed-signed-signed-signed-signed-signed-signed.pdf", "resolucion"),
    ("knowledge/resoluciones/Resolución_régimen_de_transición_20-11-2025-signed-signed-signed-signed-signed-signed-signed-signed-signed.pdf", "resolucion"),
    ("knowledge/resoluciones/instructivo_-_extorsion_firmado-DG-signed-signed.pdf",                   "resolucion"),
    ("knowledge/resoluciones/normativa-secundaria-actualizada-1.pdf",                                 "reglamento"),
    ("knowledge/resoluciones/resolucion_y_modelo_de_pliego_sicae_ok-signed-signed.pdf",               "resolucion"),
]

async def main():
    print("=" * 60)
    print("  INGESTIÓN SERCOP → sercop_db (192.168.2.2)")
    print("=" * 60)

    await inicializar_db()
    print("✓ BD inicializada\n")

    total_chunks = 0
    errores = []

    for i, (ruta, tipo) in enumerate(ARCHIVOS, 1):
        nombre = Path(ruta).stem[:50]
        print(f"[{i:02d}/{len(ARCHIVOS)}] {nombre}")
        print(f"         tipo: {tipo}")

        resultado = await ingestar_archivo(ruta, tipo=tipo)

        status = resultado.get("status")
        chunks = resultado.get("chunks", 0)

        if status == "ok":
            print(f"         ✅ {chunks} chunks indexados")
            total_chunks += chunks
        elif status == "ya_existia":
            print(f"         ⏭  ya existía ({chunks} chunks)")
            total_chunks += chunks
        else:
            detalle = resultado.get("detalle", "error desconocido")
            print(f"         ❌ ERROR: {detalle}")
            errores.append((nombre, detalle))

        print()

    print("=" * 60)
    print(f"  TOTAL: {total_chunks} chunks en {len(ARCHIVOS) - len(errores)}/{len(ARCHIVOS)} documentos")
    if errores:
        print(f"\n  Errores ({len(errores)}):")
        for nombre, detalle in errores:
            print(f"    - {nombre}: {detalle}")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
