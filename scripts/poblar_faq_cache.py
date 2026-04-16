#!/usr/bin/env python3
"""
scripts/poblar_faq_cache.py — Genera respuestas con el LLM para un banco de preguntas
y las almacena en config/faq_cache.yaml.

Usa Claude Haiku directamente (API key ya configurada) + RAG si PostgreSQL disponible.
Paralelismo controlado: semáforo de 6 llamadas simultáneas.

Uso:
    python scripts/poblar_faq_cache.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import unicodedata
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv
load_dotenv()

# ── Banco de preguntas ────────────────────────────────────────────────────────
# 90 preguntas organizadas por tema. Se llamará al LLM para cada una.

PREGUNTAS: list[dict] = [
    # ── RUP ──────────────────────────────────────────────────────────────────
    {"pregunta": "¿Qué es el RUP y para qué sirve?",
     "keywords": ["rup", "registro", "proveedores", "sirve"]},
    {"pregunta": "¿Cómo me registro en el RUP paso a paso?",
     "keywords": ["rup", "registro", "como", "pasos", "inscripcion"]},
    {"pregunta": "¿Cuánto cuesta registrarse en el RUP?",
     "keywords": ["rup", "cuanto", "cuesta", "costo", "gratis"]},
    {"pregunta": "¿Cuándo debo renovar mi RUP?",
     "keywords": ["rup", "renovar", "renovacion", "cuando", "enero"]},
    {"pregunta": "¿Qué documentos necesita una persona natural para el RUP?",
     "keywords": ["rup", "persona", "natural", "documentos", "requisitos"]},
    {"pregunta": "¿Qué documentos necesita una empresa para registrarse en el RUP?",
     "keywords": ["rup", "empresa", "juridica", "documentos", "requisitos"]},
    {"pregunta": "¿Por qué me pueden suspender del RUP?",
     "keywords": ["rup", "suspension", "suspender", "causas", "inhabilitacion"]},
    {"pregunta": "¿Cómo me habilito nuevamente en el RUP después de una suspensión?",
     "keywords": ["rup", "habilitar", "habilitacion", "suspension", "reactivar"]},
    {"pregunta": "¿Puedo contratar con el Estado sin RUP?",
     "keywords": ["rup", "sin", "contratar", "estado", "obligatorio"]},
    {"pregunta": "¿El RUP tiene categorías o clasificaciones de actividad?",
     "keywords": ["rup", "categorias", "clasificacion", "actividad", "ciiu"]},

    # ── SIE — Subasta Inversa Electrónica ─────────────────────────────────────
    {"pregunta": "¿Qué es la Subasta Inversa Electrónica (SIE)?",
     "keywords": ["sie", "subasta", "inversa", "electronica", "que"]},
    {"pregunta": "¿Cómo participo en una subasta inversa electrónica?",
     "keywords": ["sie", "subasta", "como", "participar", "pasos"]},
    {"pregunta": "¿Cuánto dura la puja en una subasta inversa?",
     "keywords": ["sie", "subasta", "puja", "duracion", "minutos"]},
    {"pregunta": "¿Cuáles son los requisitos para participar en la SIE?",
     "keywords": ["sie", "subasta", "requisitos", "participar"]},
    {"pregunta": "¿Qué pasa si solo hay un oferente en la subasta inversa?",
     "keywords": ["sie", "subasta", "un", "solo", "oferente", "unico"]},
    {"pregunta": "¿Cómo se adjudica una subasta inversa electrónica?",
     "keywords": ["sie", "subasta", "adjudicacion", "como", "adjudica"]},
    {"pregunta": "¿Qué bienes y servicios se contratan por subasta inversa?",
     "keywords": ["sie", "subasta", "bienes", "servicios", "normalizados"]},

    # ── Ínfima cuantía ────────────────────────────────────────────────────────
    {"pregunta": "¿Qué es la ínfima cuantía?",
     "keywords": ["infima", "cuantia", "que", "es", "proceso"]},
    {"pregunta": "¿Cuál es el monto máximo de ínfima cuantía en 2025?",
     "keywords": ["infima", "cuantia", "monto", "maximo", "2025"]},
    {"pregunta": "¿Necesito RUP para vender por ínfima cuantía?",
     "keywords": ["infima", "cuantia", "rup", "necesito", "proveedor"]},
    {"pregunta": "¿Qué documentos piden para ínfima cuantía?",
     "keywords": ["infima", "cuantia", "documentos", "factura", "requisitos"]},
    {"pregunta": "¿Puedo hacer varias ínfimas cuantías seguidas para el mismo objeto?",
     "keywords": ["infima", "cuantia", "varias", "repetir", "fraccionamiento"]},

    # ── Menor cuantía ─────────────────────────────────────────────────────────
    {"pregunta": "¿Qué es la menor cuantía en contratación pública?",
     "keywords": ["menor", "cuantia", "que", "es", "proceso"]},
    {"pregunta": "¿Cuál es el monto máximo de menor cuantía para bienes y servicios en 2025?",
     "keywords": ["menor", "cuantia", "monto", "bienes", "servicios", "2025"]},
    {"pregunta": "¿Cuál es el monto máximo de menor cuantía para obras en 2025?",
     "keywords": ["menor", "cuantia", "obra", "monto", "2025"]},
    {"pregunta": "¿Cómo me entero si me invitan a participar en menor cuantía?",
     "keywords": ["menor", "cuantia", "invitacion", "notificacion", "enterarme"]},
    {"pregunta": "¿Cuántos días tengo para presentar oferta en menor cuantía?",
     "keywords": ["menor", "cuantia", "dias", "plazo", "oferta"]},

    # ── Cotización ────────────────────────────────────────────────────────────
    {"pregunta": "¿Qué es la cotización en contratación pública?",
     "keywords": ["cotizacion", "que", "es", "proceso"]},
    {"pregunta": "¿Cuál es la diferencia entre cotización y menor cuantía?",
     "keywords": ["cotizacion", "menor", "cuantia", "diferencia", "cuando"]},
    {"pregunta": "¿Cuántos días dura un proceso de cotización?",
     "keywords": ["cotizacion", "dias", "duracion", "plazo", "proceso"]},
    {"pregunta": "¿Qué documentos debo presentar en una cotización?",
     "keywords": ["cotizacion", "documentos", "oferta", "presentar"]},

    # ── Licitación ────────────────────────────────────────────────────────────
    {"pregunta": "¿Qué es la licitación en contratación pública?",
     "keywords": ["licitacion", "que", "es", "proceso"]},
    {"pregunta": "¿Cuánto es el monto mínimo para licitación de bienes y servicios?",
     "keywords": ["licitacion", "monto", "minimo", "bienes", "servicios"]},
    {"pregunta": "¿Cuánto tiempo dura un proceso de licitación?",
     "keywords": ["licitacion", "tiempo", "duracion", "dias", "proceso"]},
    {"pregunta": "¿Puedo impugnar la calificación de ofertas en una licitación?",
     "keywords": ["licitacion", "impugnar", "calificacion", "recurso"]},

    # ── Catálogo electrónico ──────────────────────────────────────────────────
    {"pregunta": "¿Qué es el catálogo electrónico del SERCOP?",
     "keywords": ["catalogo", "electronico", "que", "sercop"]},
    {"pregunta": "¿Cómo ingreso mis productos al catálogo electrónico?",
     "keywords": ["catalogo", "electronico", "ingresar", "productos", "como"]},
    {"pregunta": "¿Qué ventajas tiene estar en el catálogo electrónico?",
     "keywords": ["catalogo", "electronico", "ventajas", "beneficios"]},
    {"pregunta": "¿Qué es el convenio marco en el catálogo electrónico?",
     "keywords": ["catalogo", "electronico", "convenio", "marco"]},

    # ── Consultoría ───────────────────────────────────────────────────────────
    {"pregunta": "¿Qué es la contratación de consultoría?",
     "keywords": ["consultoria", "que", "es", "proceso", "servicio"]},
    {"pregunta": "¿Cuáles son los tipos de consultoría en la LOSNCP?",
     "keywords": ["consultoria", "tipos", "modalidades", "losncp"]},
    {"pregunta": "¿Cuál es el monto para contratación directa de consultoría?",
     "keywords": ["consultoria", "contratacion", "directa", "monto"]},
    {"pregunta": "¿Qué es la lista corta en consultoría?",
     "keywords": ["consultoria", "lista", "corta", "preseleccion"]},

    # ── Régimen especial y emergencia ─────────────────────────────────────────
    {"pregunta": "¿Qué es el régimen especial en contratación pública?",
     "keywords": ["regimen", "especial", "que", "es", "cuando"]},
    {"pregunta": "¿Qué es la contratación de emergencia?",
     "keywords": ["emergencia", "contratacion", "que", "es", "cuando"]},
    {"pregunta": "¿Quién puede declarar la emergencia para contratar?",
     "keywords": ["emergencia", "declarar", "quien", "autoridad"]},

    # ── Ferias inclusivas y MIPYMES ───────────────────────────────────────────
    {"pregunta": "¿Qué es una feria inclusiva y quién puede participar?",
     "keywords": ["feria", "inclusiva", "que", "participar", "eps"]},
    {"pregunta": "¿Qué preferencias tienen las MIPYMES en contratación pública?",
     "keywords": ["mipymes", "preferencias", "beneficios", "contratacion"]},
    {"pregunta": "¿Qué es la Economía Popular y Solidaria (EPS) en contratación?",
     "keywords": ["eps", "economia", "popular", "solidaria", "contratacion"]},
    {"pregunta": "¿Qué porcentaje de preferencia tienen los proveedores nacionales?",
     "keywords": ["nacional", "preferencia", "porcentaje", "proveedor"]},

    # ── Garantías ─────────────────────────────────────────────────────────────
    {"pregunta": "¿Cuáles son los tipos de garantías en contratación pública?",
     "keywords": ["garantias", "tipos", "fiel", "cumplimiento", "anticipo"]},
    {"pregunta": "¿Cuándo debo presentar la garantía de fiel cumplimiento?",
     "keywords": ["garantia", "fiel", "cumplimiento", "cuando", "presentar"]},
    {"pregunta": "¿Qué porcentaje es la garantía de fiel cumplimiento?",
     "keywords": ["garantia", "fiel", "cumplimiento", "porcentaje", "cuanto"]},
    {"pregunta": "¿Cuándo me devuelven la garantía de fiel cumplimiento?",
     "keywords": ["garantia", "fiel", "cumplimiento", "devolucion", "cuando"]},
    {"pregunta": "¿Qué es la garantía de buen uso del anticipo?",
     "keywords": ["garantia", "buen", "uso", "anticipo", "que"]},
    {"pregunta": "¿Qué documentos sirven como garantía en contratos públicos?",
     "keywords": ["garantia", "documentos", "tipos", "poliza", "fianza"]},
    {"pregunta": "¿Debo presentar garantía técnica en una licitación?",
     "keywords": ["garantia", "tecnica", "licitacion", "cuando", "porcentaje"]},

    # ── Contratos ─────────────────────────────────────────────────────────────
    {"pregunta": "¿Cuántos días tengo para firmar el contrato después de ser adjudicado?",
     "keywords": ["contrato", "firmar", "dias", "adjudicacion", "plazo"]},
    {"pregunta": "¿Qué es el anticipo en un contrato público?",
     "keywords": ["anticipo", "contrato", "que", "es", "porcentaje"]},
    {"pregunta": "¿Cuánto es el anticipo máximo en un contrato?",
     "keywords": ["anticipo", "contrato", "maximo", "porcentaje", "cuanto"]},
    {"pregunta": "¿Puedo modificar o reformar un contrato ya firmado?",
     "keywords": ["contrato", "modificar", "reforma", "adendum", "cambio"]},
    {"pregunta": "¿Qué pasa si no puedo cumplir el contrato que gané?",
     "keywords": ["contrato", "incumplimiento", "no", "cumplir", "consecuencias"]},
    {"pregunta": "¿Qué es el acta de entrega-recepción provisional y definitiva?",
     "keywords": ["acta", "entrega", "recepcion", "provisional", "definitiva"]},
    {"pregunta": "¿Cómo se termina anticipadamente un contrato público?",
     "keywords": ["contrato", "terminacion", "anticipada", "como", "causas"]},
    {"pregunta": "¿Qué es la liquidación de contrato?",
     "keywords": ["liquidacion", "contrato", "que", "es", "como"]},

    # ── Impugnaciones y recursos ──────────────────────────────────────────────
    {"pregunta": "¿Puedo impugnar la adjudicación de un proceso?",
     "keywords": ["impugnar", "adjudicacion", "recurso", "como", "puedo"]},
    {"pregunta": "¿Cuántos días tengo para impugnar una adjudicación?",
     "keywords": ["impugnacion", "dias", "plazo", "adjudicacion", "tiempo"]},
    {"pregunta": "¿El recurso de apelación suspende el proceso de contratación?",
     "keywords": ["recurso", "apelacion", "suspende", "proceso", "contratacion"]},
    {"pregunta": "¿Qué hago si la entidad no responde mi recurso de apelación?",
     "keywords": ["recurso", "apelacion", "no", "responden", "que", "hago"]},
    {"pregunta": "¿Puedo impugnar si no me calificaron en un proceso?",
     "keywords": ["impugnar", "calificacion", "descalificado", "rechazado"]},

    # ── PAC ───────────────────────────────────────────────────────────────────
    {"pregunta": "¿Qué es el PAC (Plan Anual de Contratación)?",
     "keywords": ["pac", "plan", "anual", "contratacion", "que"]},
    {"pregunta": "¿Cuándo deben publicar el PAC las entidades contratantes?",
     "keywords": ["pac", "cuando", "publicar", "enero", "fecha"]},
    {"pregunta": "¿Cómo busco el PAC de una entidad en el portal?",
     "keywords": ["pac", "buscar", "portal", "compraspublicas", "entidad"]},
    {"pregunta": "¿Las entidades pueden modificar el PAC durante el año?",
     "keywords": ["pac", "modificar", "cambiar", "reformar", "año"]},

    # ── SOCE / Portal ComprasPúblicas ─────────────────────────────────────────
    {"pregunta": "¿Qué es el SOCE (Sistema Oficial de Contratación del Estado)?",
     "keywords": ["soce", "que", "es", "portal", "sistema"]},
    {"pregunta": "¿Cómo busco procesos activos en ComprasPúblicas?",
     "keywords": ["compraspublicas", "buscar", "procesos", "activos", "portal"]},
    {"pregunta": "¿Cómo presento mi oferta electrónicamente en el portal?",
     "keywords": ["oferta", "electronica", "presentar", "portal", "como"]},
    {"pregunta": "¿Qué información debo registrar en mi perfil del portal SOCE?",
     "keywords": ["soce", "portal", "perfil", "informacion", "registrar"]},

    # ── Normativa ─────────────────────────────────────────────────────────────
    {"pregunta": "¿Qué es la LOSNCP?",
     "keywords": ["losncp", "que", "es", "ley", "contratacion"]},
    {"pregunta": "¿Qué es el Reglamento General a la LOSNCP?",
     "keywords": ["rglosncp", "reglamento", "general", "losncp", "que"]},
    {"pregunta": "¿Dónde puedo encontrar las resoluciones del SERCOP?",
     "keywords": ["resoluciones", "sercop", "donde", "encontrar", "normativa"]},
    {"pregunta": "¿Qué es el SERCOP y cuáles son sus funciones?",
     "keywords": ["sercop", "que", "es", "funciones", "rol"]},

    # ── Sanciones ─────────────────────────────────────────────────────────────
    {"pregunta": "¿Qué sanciones existen para proveedores que incumplen contratos?",
     "keywords": ["sanciones", "proveedor", "incumplimiento", "contrato"]},
    {"pregunta": "¿Qué pasa si presento documentos falsos en una oferta?",
     "keywords": ["documentos", "falsos", "oferta", "sancion", "consecuencias"]},
    {"pregunta": "¿Cómo denuncio un posible acto de corrupción en contratación?",
     "keywords": ["denuncia", "corrupcion", "irregularidad", "como", "denunciar"]},

    # ── Preguntas prácticas con montos ────────────────────────────────────────
    {"pregunta": "Quiero vender computadoras al Estado, ¿qué proceso de contratación se usa?",
     "keywords": ["computadoras", "bienes", "proceso", "contratacion", "cual"]},
    {"pregunta": "Quiero ofrecer servicios de limpieza a una entidad pública, ¿qué proceso debo usar?",
     "keywords": ["limpieza", "servicios", "proceso", "entidad", "cual"]},
    {"pregunta": "Tengo $5.000 para contratar mantenimiento, ¿qué proceso de contratación aplica?",
     "keywords": ["mantenimiento", "5000", "proceso", "cual", "aplica"]},
    {"pregunta": "Tengo $80.000 para contratar una auditoría, ¿qué proceso debo usar?",
     "keywords": ["auditoria", "80000", "consultoria", "proceso", "cual"]},
    {"pregunta": "¿Puedo participar como consorcio en un proceso de contratación?",
     "keywords": ["consorcio", "participar", "puede", "proceso"]},
    {"pregunta": "¿Qué es un pliego de condiciones o pliego precontractual?",
     "keywords": ["pliego", "condiciones", "precontractual", "que"]},
    {"pregunta": "¿Qué pasa si hay empate en las ofertas de un proceso?",
     "keywords": ["empate", "oferta", "proceso", "que", "pasa"]},
    {"pregunta": "¿Un proveedor extranjero puede contratar con el Estado ecuatoriano?",
     "keywords": ["extranjero", "proveedor", "contratar", "ecuador", "puede"]},
    {"pregunta": "¿Cómo calculo el puntaje para una licitación?",
     "keywords": ["puntaje", "calculo", "licitacion", "calificacion", "como"]},
    {"pregunta": "¿Qué es la oferta más conveniente y cómo se elige?",
     "keywords": ["oferta", "conveniente", "mejor", "como", "elegir"]},
]


# ── Utilidades ────────────────────────────────────────────────────────────────

def _normalizar(texto: str) -> str:
    texto = texto.lower().strip()
    texto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def _kw_existentes(faqs: list[dict]) -> set[tuple]:
    """Conjuntos de keywords normalizados ya en el cache."""
    resultado = set()
    for f in faqs:
        kws = [_normalizar(str(k)) for k in f.get("keywords", [])]
        resultado.add(tuple(sorted(kws)))
    return resultado


# ── Llamada al LLM con RAG ────────────────────────────────────────────────────

SYSTEM_PROMPT_CORTO = """Eres Sercobot, asistente oficial de contratación pública del SERCOP Ecuador.
Responde de forma directa, concisa y citando la base legal cuando la conozcas.
Formato para WhatsApp: máximo 3 párrafos, usa emojis para listas y puntos clave.
Siempre cierra con la base legal en formato: 📌 Base legal: Art. X LOSNCP (si aplica)."""


async def llamar_claude_con_rag(pregunta: str) -> str:
    """Llama a Claude con contexto RAG si está disponible."""
    from anthropic import AsyncAnthropic

    contexto = ""
    try:
        from agent.retriever import recuperar_contexto_formateado
        ctx, n = await recuperar_contexto_formateado(pregunta)
        if n > 0:
            contexto = f"\n\nNormativa relevante:\n{ctx}"
    except Exception:
        pass

    mensaje_usuario = pregunta
    if contexto:
        mensaje_usuario += "\n\n---\nReferencia normativa:\n" + contexto

    client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        system=SYSTEM_PROMPT_CORTO,
        messages=[{"role": "user", "content": mensaje_usuario}],
    )
    return response.content[0].text.strip()


# ── Carga y guardado del cache ────────────────────────────────────────────────

def cargar_cache(path: Path) -> list[dict]:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("faqs", [])
    return []


def guardar_cache(path: Path, faqs: list[dict]) -> None:
    data = {"faqs": faqs}
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False,
                  sort_keys=False, width=120)


# ── Pipeline principal ────────────────────────────────────────────────────────

async def procesar_pregunta(
    item: dict,
    semaforo: asyncio.Semaphore,
    cache_path: Path,
    lock: asyncio.Lock,
    kw_existentes: set,
    contador: dict,
) -> None:
    pregunta = item["pregunta"]
    keywords = item["keywords"]
    kw_tuple = tuple(sorted(_normalizar(str(k)) for k in keywords))

    async with semaforo:
        # Verificar si ya existe en el cache (con lock para evitar race conditions)
        async with lock:
            if kw_tuple in kw_existentes:
                print(f"  ⏭  ya existe: {pregunta[:60]}")
                return

        t0 = time.time()
        try:
            respuesta = await llamar_claude_con_rag(pregunta)
            elapsed = time.time() - t0

            entrada = {
                "keywords":  keywords,
                "pregunta":  pregunta,
                "respuesta": respuesta,
                "frecuencia": 0,
            }

            async with lock:
                # Recargar para evitar conflictos si otro coroutine ya escribió
                faqs = cargar_cache(cache_path)
                kw_actuales = _kw_existentes(faqs)
                if kw_tuple not in kw_actuales:
                    faqs.append(entrada)
                    guardar_cache(cache_path, faqs)
                    kw_existentes.add(kw_tuple)
                    contador["nuevas"] += 1
                    print(f"  ✅ [{elapsed:.1f}s] {pregunta[:65]}")
                else:
                    print(f"  ⏭  race-condition skip: {pregunta[:50]}")

        except Exception as e:
            print(f"  ❌ ERROR en '{pregunta[:50]}': {e}")
            contador["errores"] += 1


async def main():
    cache_path = ROOT / "config" / "faq_cache.yaml"

    print(f"\n{'═' * 70}")
    print(f"  Sercobot — Poblado masivo del FAQ cache con Claude Haiku")
    print(f"{'═' * 70}")
    print(f"  Banco de preguntas: {len(PREGUNTAS)}")
    print(f"  Cache: {cache_path}")
    print(f"  Paralelismo: 6 llamadas simultáneas\n")

    faqs_actuales = cargar_cache(cache_path)
    kw_existentes = _kw_existentes(faqs_actuales)
    pendientes = [
        p for p in PREGUNTAS
        if tuple(sorted(_normalizar(str(k)) for k in p["keywords"])) not in kw_existentes
    ]

    print(f"  Ya en cache: {len(faqs_actuales)}")
    print(f"  Pendientes:  {len(pendientes)}\n")

    if not pendientes:
        print("✅ Todas las preguntas ya están en el cache.")
        return

    semaforo = asyncio.Semaphore(6)
    lock = asyncio.Lock()
    contador = {"nuevas": 0, "errores": 0}

    tareas = [
        procesar_pregunta(item, semaforo, cache_path, lock, kw_existentes, contador)
        for item in pendientes
    ]

    t_total = time.time()
    await asyncio.gather(*tareas)
    elapsed_total = time.time() - t_total

    faqs_final = cargar_cache(cache_path)
    print(f"\n{'═' * 70}")
    print(f"  Completado en {elapsed_total:.1f}s")
    print(f"  Nuevas entradas agregadas: {contador['nuevas']}")
    print(f"  Errores:                   {contador['errores']}")
    print(f"  Total en cache ahora:      {len(faqs_final)}")
    print(f"{'═' * 70}\n")


if __name__ == "__main__":
    asyncio.run(main())
