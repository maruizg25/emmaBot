#!/usr/bin/env python3
"""
scripts/analizar_faqs.py — Análisis de preguntas frecuentes y actualización del FAQ cache.

Uso:
    python scripts/analizar_faqs.py [--dias 7|30] [--min-frecuencia 10] [--actualizar]

Funciones:
    - Agrupa preguntas similares por keywords (últimos 7 o 30 días)
    - Cuenta frecuencia de cada patrón
    - Identifica preguntas repetidas > --min-frecuencia veces
    - Con --actualizar: regenera config/faq_cache.yaml con los nuevos patrones
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import unicodedata
import re
import yaml
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Asegurar que el directorio raíz del proyecto esté en el path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.chdir(ROOT)

from dotenv import load_dotenv
load_dotenv()


# ─── Utilidades ──────────────────────────────────────────────────────────────

def _normalizar(texto: str) -> str:
    texto = texto.lower().strip()
    texto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


_STOPWORDS = {_normalizar(w) for w in {
    "el", "la", "los", "las", "de", "que", "qué", "es", "un", "una",
    "me", "para", "en", "con", "a", "y", "o", "por", "del", "al",
    "le", "se", "mi", "tu", "su", "lo", "hay", "tiene", "tengo",
    "cual", "como", "cuando", "donde", "quien", "cuanto",
}}


def _extraer_keywords(texto_norm: str) -> list[str]:
    tokens = [t for t in texto_norm.split() if t not in _STOPWORDS and len(t) > 2]
    return tokens[:8]  # Máx 8 keywords por pregunta


def _similitud_keywords(kw1: list[str], kw2: list[str]) -> float:
    if not kw1 or not kw2:
        return 0.0
    s1, s2 = set(kw1), set(kw2)
    return len(s1 & s2) / max(len(s1), len(s2))


# ─── Consulta a la base de datos ─────────────────────────────────────────────

async def obtener_preguntas(dias: int) -> list[dict]:
    """Obtiene preguntas recientes de consultas_log."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    db_url = os.getenv("DATABASE_URL", "")
    if not db_url.startswith("postgresql"):
        print("⚠️  Este script requiere PostgreSQL. DATABASE_URL no apunta a Postgres.")
        return []

    engine = create_async_engine(db_url, echo=False)
    desde = datetime.now(timezone.utc) - timedelta(days=dias)

    async with engine.connect() as conn:
        result = await conn.execute(text("""
            SELECT pregunta_normalizada, COUNT(*) AS frecuencia
            FROM consultas_log
            WHERE timestamp >= :desde
              AND NOT fue_shortcut
              AND pregunta_normalizada IS NOT NULL
              AND LENGTH(pregunta_normalizada) > 10
            GROUP BY pregunta_normalizada
            ORDER BY frecuencia DESC
            LIMIT 500
        """), {"desde": desde})
        rows = result.fetchall()

    await engine.dispose()
    return [{"pregunta": r.pregunta_normalizada, "frecuencia": r.frecuencia} for r in rows]


# ─── Agrupación por keywords ─────────────────────────────────────────────────

def agrupar_por_keywords(preguntas: list[dict], umbral_sim: float = 0.6) -> list[dict]:
    """
    Agrupa preguntas similares (similitud de keywords ≥ umbral_sim).
    Retorna grupos con la pregunta representativa y frecuencia total.
    """
    grupos: list[dict] = []
    usados: set[int] = set()

    for i, p in enumerate(preguntas):
        if i in usados:
            continue
        kw_i = _extraer_keywords(p["pregunta"])
        grupo_freq = p["frecuencia"]
        grupo_preg = [p["pregunta"]]
        grupo_idx = [i]

        for j, q in enumerate(preguntas):
            if j <= i or j in usados:
                continue
            kw_j = _extraer_keywords(q["pregunta"])
            if _similitud_keywords(kw_i, kw_j) >= umbral_sim:
                grupo_freq += q["frecuencia"]
                grupo_preg.append(q["pregunta"])
                grupo_idx.append(j)

        for idx in grupo_idx:
            usados.add(idx)

        # La pregunta representativa es la más frecuente del grupo
        pregunta_repr = preguntas[grupo_idx[0]]["pregunta"]
        keywords_repr = _extraer_keywords(pregunta_repr)

        grupos.append({
            "keywords":    keywords_repr,
            "pregunta":    pregunta_repr,
            "variantes":   len(grupo_preg),
            "frecuencia":  grupo_freq,
        })

    return sorted(grupos, key=lambda x: x["frecuencia"], reverse=True)


# ─── Actualización del cache ──────────────────────────────────────────────────

def actualizar_faq_cache(grupos: list[dict], min_freq: int, cache_path: Path) -> int:
    """
    Agrega al FAQ cache los patrones con frecuencia ≥ min_freq.
    No sobrescribe entradas existentes.
    Retorna el número de entradas nuevas agregadas.
    """
    # Cargar cache existente
    if cache_path.exists():
        with open(cache_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}

    faqs_existentes = data.get("faqs", [])
    kw_existentes = {tuple(sorted(f.get("keywords", []))) for f in faqs_existentes}

    nuevas = 0
    for grupo in grupos:
        if grupo["frecuencia"] < min_freq:
            continue
        kw_tuple = tuple(sorted(grupo["keywords"]))
        if kw_tuple in kw_existentes:
            continue

        faqs_existentes.append({
            "keywords":  grupo["keywords"],
            "pregunta":  grupo["pregunta"],
            "respuesta": (
                f"[Respuesta pendiente — esta pregunta apareció {grupo['frecuencia']} veces. "
                f"Completar manualmente en {cache_path.name}]"
            ),
            "frecuencia": grupo["frecuencia"],
        })
        nuevas += 1

    data["faqs"] = faqs_existentes
    with open(cache_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    return nuevas


# ─── Main ─────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(
        description="Analiza preguntas frecuentes y actualiza el FAQ cache de Sercobot"
    )
    parser.add_argument("--dias",            type=int, default=7,
                        help="Período de análisis en días (default: 7)")
    parser.add_argument("--min-frecuencia",  type=int, default=10,
                        help="Frecuencia mínima para agregar al cache (default: 10)")
    parser.add_argument("--actualizar",      action="store_true",
                        help="Actualizar faq_cache.yaml con los patrones encontrados")
    args = parser.parse_args()

    print(f"\n🔍 Analizando preguntas de los últimos {args.dias} días...\n")

    preguntas = await obtener_preguntas(args.dias)
    if not preguntas:
        print("⚠️  Sin datos — no hay consultas registradas en consultas_log.")
        return

    print(f"📊 Total de patrones únicos encontrados: {len(preguntas)}")
    print(f"📊 Total de consultas en el período: {sum(p['frecuencia'] for p in preguntas)}\n")

    grupos = agrupar_por_keywords(preguntas, umbral_sim=0.6)

    # Filtrar por frecuencia mínima
    frecuentes = [g for g in grupos if g["frecuencia"] >= args.min_frecuencia]

    print(f"{'─' * 70}")
    print(f"{'PREGUNTA':<50} {'FREC':>6} {'VARIANTES':>9}")
    print(f"{'─' * 70}")
    for g in frecuentes[:30]:
        pregunta_corta = g["pregunta"][:48] + ".." if len(g["pregunta"]) > 50 else g["pregunta"]
        print(f"{pregunta_corta:<50} {g['frecuencia']:>6} {g['variantes']:>9}")
    print(f"{'─' * 70}")
    print(f"\n📌 {len(frecuentes)} patrones con frecuencia ≥ {args.min_frecuencia}")

    if args.actualizar:
        cache_path = ROOT / "config" / "faq_cache.yaml"
        nuevas = actualizar_faq_cache(frecuentes, args.min_frecuencia, cache_path)
        if nuevas:
            print(f"\n✅ {nuevas} nuevas entradas agregadas a {cache_path.name}")
            print("⚠️  Recuerda completar las respuestas pendientes manualmente.")
        else:
            print(f"\n✅ El cache ya contiene todos los patrones frecuentes.")
    else:
        print(
            f"\n💡 Para actualizar el cache ejecuta:\n"
            f"   python scripts/analizar_faqs.py --dias {args.dias} "
            f"--min-frecuencia {args.min_frecuencia} --actualizar"
        )


if __name__ == "__main__":
    asyncio.run(main())
