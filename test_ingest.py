import asyncio, logging, sys
logging.basicConfig(level=logging.INFO, stream=sys.stdout)

async def test():
    from agent.memory import inicializar_db
    await inicializar_db()
    from agent.ingestion import ingestar_archivo

    pdfs = [
        ('knowledge/2.-Resolución-Nro.-R.E-SERCOP-2026-0002-Expide-Norma-Interna-de-Funcionamiento-del-Comite-Interinstitucional.pdf', 'resolucion'),
        ('knowledge/ANEXO-1-GLOSARIO-DE-TERMINOS.pdf', 'faq'),
        ('knowledge/LOSNCP-RO-140-07-X-2025-1.pdf', 'ley'),
        ('knowledge/resoluciones/1.-RESOLUCIÓN-R.E-SERCOP-2026-0001-METODOLOGÍA-CONTROL.pdf', 'resolucion'),
        ('knowledge/resoluciones/1.1-METODOLOGÍA-DE-CONTROL-FINAL-SUMILLADA-1.pdf', 'resolucion'),
        ('knowledge/resoluciones/2.1-Norma-Interna-de-Funcionamiento-de-la-Subasta-Inversa-Corporativa-de-fármacos-y-biens-estregicos-en-salud-Anexo-Resolución-R.E-SERCOP-2026-0002-1.pdf', 'resolucion'),
        ('knowledge/resoluciones/FE-DE-ERRATA-RESOLUCION-No.-RE-SERCOP-2024-0142.pdf', 'resolucion'),
        ('knowledge/resoluciones/R.E-SERCOP-2025-0152-signed-signed-2.pdf', 'resolucion'),
        ('knowledge/resoluciones/R.E-SERCOP-2025-0152-signed-signed.pdf', 'resolucion'),
        ('knowledge/resoluciones/RESOLUCION-Nro.-RE-SERCOP-2024-0144.pdf', 'resolucion'),
        ('knowledge/resoluciones/Resolucion-cod-etica-23-3-26-signed-signed-signed-signed-signed-signed-signed.pdf', 'resolucion'),
        ('knowledge/resoluciones/Resolución_régimen_de_transición_20-11-2025-signed-signed-signed-signed-signed-signed-signed-signed-signed.pdf', 'resolucion'),
        ('knowledge/resoluciones/instructivo_-_extorsion_firmado-DG-signed-signed.pdf', 'resolucion'),
        ('knowledge/resoluciones/normativa-secundaria-actualizada-1.pdf', 'ley'),
        ('knowledge/resoluciones/resolucion_y_modelo_de_pliego_sicae_ok-signed-signed.pdf', 'resolucion'),
    ]

    total_chunks = 0
    for ruta, tipo in pdfs:
        import os
        if not os.path.exists(ruta):
            print(f"  [SKIP] {ruta}")
            continue
        nombre = os.path.basename(ruta)
        print(f"  → {nombre[:60]}...", flush=True)
        result = await ingestar_archivo(ruta, tipo=tipo)
        status = result.get('status', '?')
        chunks = result.get('chunks', 0)
        print(f"     {status} — {chunks} chunks", flush=True)
        total_chunks += chunks

    print(f"\nTotal chunks indexados: {total_chunks}", flush=True)

asyncio.run(test())
