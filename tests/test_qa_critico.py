#!/usr/bin/env python3
"""
Suite QA Crítica — Sercobot
===========================
Tres grupos de tests progresivos:

  Grupo A — Routing puro (0ms, sin LLM)
    Verifica que _detectar_shortcut() clasifica correctamente saludos,
    despedidas, FAQ hits, short queries, scope y multimedia.

  Grupo B — Contenido FAQ (0ms, sin LLM)
    Verifica que las respuestas FAQ contienen los hechos clave correctos.
    Detecta respuestas de la categoría equivocada aunque la categoría sea "faq_cache".

  Grupo C — Hechos críticos vía LLM (requiere API)
    Verifica que el LLM no alucina sobre puja, montos, gratuidad del RUP, etc.
    Solo se ejecuta cuando --llm está en los argumentos o LLM_QA=1.

Anti-regresiones: cada bug conocido tiene su caso etiquetado [REGR].

Uso:
    python tests/test_qa_critico.py          # solo grupos A y B (rápido)
    python tests/test_qa_critico.py --llm    # incluye grupo C (lento, llama APIs)
    python tests/test_qa_critico.py --grupo A
    python tests/test_qa_critico.py --grupo B
    python tests/test_qa_critico.py --grupo C --llm
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv
load_dotenv()

# ── Colores ANSI ──────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"


# ── Estructura de un caso de prueba ──────────────────────────────────────────

@dataclass
class Caso:
    grupo: str           # "A", "B" o "C"
    mensaje: str
    desc: str            # descripción del test
    cat_esperada: Optional[str] = None   # categoría esperada del shortcut
    contiene: list[str] = field(default_factory=list)    # todas deben estar en la respuesta (case-insensitive)
    no_contiene: list[str] = field(default_factory=list) # ninguna debe estar
    regresion: Optional[str] = None  # etiqueta del bug original si es anti-regresión


# ── Definición de todos los casos ─────────────────────────────────────────────

CASOS: list[Caso] = [

    # ═══════════════════════════════════════════════════════════════════════════
    # GRUPO A — Routing puro (sin LLM)
    # ═══════════════════════════════════════════════════════════════════════════

    # Saludos
    Caso("A", "hola",            "saludo simple",       cat_esperada="saludo"),
    Caso("A", "buenos días",     "saludo con tilde",    cat_esperada="saludo"),
    Caso("A", "hola! 👋",        "saludo con emoji",    cat_esperada="saludo"),
    Caso("A", "inicio",          "keyword inicio→menú", cat_esperada="saludo"),
    Caso("A", "menu",            "keyword menú",        cat_esperada="saludo"),

    # Despedidas / agradecimientos
    Caso("A", "gracias",         "agradecimiento",      cat_esperada="agradecimiento"),
    Caso("A", "muchas gracias",  "agradecimiento frase",cat_esperada="agradecimiento"),
    Caso("A", "adiós",           "despedida",           cat_esperada="despedida"),
    Caso("A", "bye",             "despedida en inglés", cat_esperada="despedida"),

    # Afirmaciones / negaciones
    Caso("A", "ok",              "afirmación",          cat_esperada="afirmacion"),
    Caso("A", "si",              "afirmación simple",   cat_esperada="afirmacion"),
    Caso("A", "no",              "negación simple",     cat_esperada="negacion"),

    # Menú numerado
    Caso("A", "1",               "menú ítem 1",         cat_esperada="faq_cache"),
    Caso("A", "2",               "menú ítem 2",         cat_esperada="faq_cache"),
    Caso("A", "3",               "menú ítem 3",         cat_esperada="faq_cache"),
    Caso("A", "4",               "menú ítem 4",         cat_esperada="faq_cache"),
    Caso("A", "5",               "menú ítem 5",         cat_esperada="faq_cache"),

    # Multimedia
    Caso("A", "[audio]",         "mensaje de voz",      cat_esperada="media"),
    Caso("A", "[imagen]",        "imagen adjunta",      cat_esperada="media"),
    Caso("A", "[sticker]",       "sticker",             cat_esperada="media"),

    # Vacíos / emojis solos
    Caso("A", "😊",              "emoji solo",          cat_esperada="emoji_vacio"),
    Caso("A", "   ",             "espacios vacíos",     cat_esperada="emoji_vacio"),

    # Fuera de scope
    Caso("A", "qué película me recomiendas",  "cine",       cat_esperada="fuera_scope"),
    Caso("A", "cuál es la capital de Francia","geografía",   cat_esperada="fuera_scope"),
    Caso("A", "dame una receta de arroz",     "receta",      cat_esperada="fuera_scope"),

    # Short queries → consulta_ambigua  [REGR: bugs #3 y #4]
    Caso("A", "procesos",        "token genérico → menú [REGR-BUG3]",
         cat_esperada="consulta_ambigua",
         regresion="BUG3: 'procesos' devolvía Cotización"),
    Caso("A", "proceso",         "token genérico → menú [REGR-BUG3]",
         cat_esperada="consulta_ambigua",
         regresion="BUG3: 'proceso' devolvía Cotización"),
    Caso("A", "que es un proceso","1 token meaningful → menú [REGR-BUG3]",
         cat_esperada="consulta_ambigua",
         regresion="BUG3: devolvía Cotización vía single-token fallback"),

    # FAQ hits — categoría correcta
    Caso("A", "que es el RUP",         "FAQ RUP",          cat_esperada="faq_cache"),
    Caso("A", "que es una SIE",        "FAQ SIE [REGR-BUG1]", cat_esperada="faq_cache",
         regresion="BUG1: devolvía RUP por keywords 'que'+'es'"),
    Caso("A", "que es la puja",        "FAQ puja [REGR-BUG2]", cat_esperada="faq_cache",
         regresion="BUG2: devolvía RUP"),
    Caso("A", "en que procesos existe puja", "FAQ puja+procesos [REGR-BUG4]",
         cat_esperada="faq_cache",
         regresion="BUG4: devolvía Cotización"),
    Caso("A", "cuanto cuesta el RUP",  "FAQ costo RUP",    cat_esperada="faq_cache"),
    Caso("A", "licitacion",            "FAQ licitación",   cat_esperada="faq_cache"),

    # ═══════════════════════════════════════════════════════════════════════════
    # GRUPO B — Contenido FAQ (sin LLM)
    # ═══════════════════════════════════════════════════════════════════════════

    # RUP
    Caso("B", "cuanto cuesta el RUP",
         "RUP es gratuito",
         cat_esperada="faq_cache",
         contiene=["gratuito"],
         no_contiene=["$100", "$50", "$200"]),

    Caso("B", "que es el RUP",
         "RUP es registro de proveedores",
         cat_esperada="faq_cache",
         contiene=["proveedor", "registro"]),

    Caso("B", "como me registro en el RUP",
         "FAQ registro RUP — contiene info de registro",
         cat_esperada="faq_cache",
         contiene=["compraspublicas"]),

    # SIE — NO debe decir RUP [REGR-BUG1]
    Caso("B", "que es una SIE",
         "SIE es subasta, NO es RUP [REGR-BUG1]",
         cat_esperada="faq_cache",
         contiene=["subasta", "precio"],
         no_contiene=["Registro Único de Proveedores"],
         regresion="BUG1: respuesta era el RUP FAQ completo"),

    Caso("B", "sie",
         "SIE keyword solo → SIE definition",
         cat_esperada="faq_cache",
         contiene=["subasta"]),

    # Puja — solo en SIE, NUNCA en licitación/cotización [REGR-BUG2 y BUG4]
    Caso("B", "que es la puja",
         "puja solo en SIE [REGR-BUG2]",
         cat_esperada="faq_cache",
         contiene=["SIE", "subasta"],
         no_contiene=["licitación tiene puja", "cotización tiene puja"],
         regresion="BUG2: devolvía RUP en vez de puja"),

    Caso("B", "en que procesos existe puja",
         "puja ÚNICAMENTE en SIE [REGR-BUG4]",
         cat_esperada="faq_cache",
         contiene=["únicamente", "SIE"],
         # "licitación" aparece en el FAQ en contexto NEGATIVO ("NO tienen puja: Licitación...")
         # así que no lo baneamos; verificamos que NO diga que SÍ existe
         no_contiene=["en licitación existe puja", "cotización tiene puja"],
         regresion="BUG4: devolvía Cotización"),

    Caso("B", "como se puja en contratacion publica",
         "puja en SIE — acepta FAQ o LLM",
         # No forzamos cat porque "contratacion publica" puede ir a FAQ o LLM
         contiene=["puja"]),

    # Garantías
    Caso("B", "que garantias existen",
         "tipos de garantías",
         cat_esperada="faq_cache",
         contiene=["fiel cumplimiento"]),

    Caso("B", "cuanto es la garantia de fiel cumplimiento",
         "garantía fiel cumplimiento = 5%",
         cat_esperada="faq_cache",
         contiene=["5%"]),

    # Licitación
    Caso("B", "licitacion",
         "licitación FAQ",
         cat_esperada="faq_cache",
         contiene=["licitaci"]),

    # Short queries → menú, NO contenido de algún proceso [REGR-BUG3]
    Caso("B", "procesos",
         "menú, NO Cotización [REGR-BUG3]",
         cat_esperada="consulta_ambigua",
         no_contiene=["La cotización", "cotización es el proceso"],
         regresion="BUG3: devolvía definición de Cotización"),

    Caso("B", "que es un proceso",
         "menú, NO Cotización [REGR-BUG3]",
         cat_esperada="consulta_ambigua",
         no_contiene=["La cotización"],
         regresion="BUG3"),

    # Variantes con typos / mayúsculas / sin tilde
    Caso("B", "que es el rup",        "RUP minúsculas",        cat_esperada="faq_cache", contiene=["RUP"]),
    Caso("B", "QUE ES EL RUP",        "RUP mayúsculas",        cat_esperada="faq_cache", contiene=["RUP"]),
    Caso("B", "cuanto cuesta inscribirme en el rup", "RUP gratuito variant", cat_esperada="faq_cache", contiene=["gratuito"]),

    # ═══════════════════════════════════════════════════════════════════════════
    # GRUPO C — Hechos críticos vía LLM (requiere --llm)
    # ═══════════════════════════════════════════════════════════════════════════

    # Anti-alucinación: puja NUNCA en licitación
    Caso("C", "en una licitación existe puja?",
         "licitación NO tiene puja",
         no_contiene=["sí existe puja", "la licitación tiene puja", "también existe puja"],
         contiene=["no"]),

    # Anti-alucinación: puja NUNCA en cotización
    Caso("C", "hay puja en la cotizacion?",
         "cotización NO tiene puja",
         no_contiene=["sí existe puja", "la cotización tiene puja"],
         contiene=["no"]),

    # Anti-alucinación: puja NUNCA en menor cuantía
    Caso("C", "en menor cuantia hay puja?",
         "menor cuantía NO tiene puja",
         no_contiene=["sí existe puja", "menor cuantía tiene puja"],
         contiene=["no"]),

    # Monto ínfima cuantía 2026
    Caso("C", "cuanto es el monto de infima cuantia en 2026",
         "ínfima cuantía 2026 = $7,400",
         contiene=["7.400", "7400"],
         no_contiene=["72.630", "72630"]),

    # Monto menor cuantía bienes 2026
    Caso("C", "cual es el monto maximo de menor cuantia para bienes en 2026",
         "menor cuantía bienes 2026 = $74,000",
         contiene=["74.000", "74000"],
         no_contiene=["72.630"]),

    # RUP gratuito vía LLM
    Caso("C", "cuanto cuesta registrarse en el RUP",
         "RUP gratuito (LLM confirm)",
         contiene=["gratuito", "0"],
         no_contiene=["$100", "$50", "$200", "costo de inscripción"]),

    # Respuesta sobre proceso equivocado
    Caso("C", "cuantos dias dura la puja en la licitacion",
         "puja no existe en licitación",
         no_contiene=["la licitación dura", "puja de la licitación"],
         contiene=["no"]),

    # SIE = bienes normalizados
    Caso("C", "para que tipo de compras se usa la SIE",
         "SIE para bienes/servicios normalizados",
         contiene=["normalizado"]),

    # No alucinar artículos
    Caso("C", "que dice el articulo 999 de la losncp",
         "artículo 999 no existe → no inventar",
         no_contiene=["El artículo 999 establece", "según el artículo 999"]),
]


# ── Motor de ejecución ────────────────────────────────────────────────────────

async def ejecutar_caso(caso: Caso, usar_llm: bool) -> dict:
    """Ejecuta un caso y retorna su resultado."""
    from agent.brain import _detectar_shortcut, generar_respuesta

    t0 = time.time()

    if caso.grupo in ("A", "B"):
        sc = _detectar_shortcut(caso.mensaje)
        cat = sc[0] if sc else "LLM"
        respuesta = sc[1] if sc else ""

        # Para grupo B con categoria LLM (no shortcut), igual evaluamos contenido
        if not sc and caso.grupo == "B":
            respuesta = await generar_respuesta(caso.mensaje, [], telefono="qa_test")
            cat = "LLM"
    else:
        # Grupo C: siempre LLM completo
        if not usar_llm:
            return {"caso": caso, "skip": True, "elapsed_ms": 0}
        sc = _detectar_shortcut(caso.mensaje)
        if sc:
            cat = sc[0]
            respuesta = sc[1]
        else:
            respuesta = await generar_respuesta(caso.mensaje, [], telefono="qa_test")
            cat = "LLM"

    elapsed_ms = int((time.time() - t0) * 1000)
    resp_lower = respuesta.lower()

    errores = []

    # Verificar categoría
    if caso.cat_esperada and cat != caso.cat_esperada:
        errores.append(f"cat={cat!r}, esperado={caso.cat_esperada!r}")

    # Verificar contenido obligatorio
    for term in caso.contiene:
        if term.lower() not in resp_lower:
            errores.append(f"falta: {term!r}")

    # Verificar contenido prohibido
    for term in caso.no_contiene:
        if term.lower() in resp_lower:
            errores.append(f"contiene (prohibido): {term!r}")

    return {
        "caso": caso,
        "cat": cat,
        "respuesta": respuesta,
        "elapsed_ms": elapsed_ms,
        "errores": errores,
        "skip": False,
    }


# ── Presentación ─────────────────────────────────────────────────────────────

def imprimir_resultado(r: dict, verbose: bool = False):
    caso = r["caso"]
    if r.get("skip"):
        print(f"  {DIM}⏭  [{caso.grupo}] {caso.desc} — omitido (requiere --llm){RESET}")
        return

    ok = not r["errores"]
    icon = f"{GREEN}✅{RESET}" if ok else f"{RED}❌{RESET}"
    regr = f" {YELLOW}[REGR]{RESET}" if caso.regresion else ""
    print(f"  {icon} [{caso.grupo}] {caso.desc}{regr}  {DIM}({r['elapsed_ms']}ms){RESET}")

    if not ok:
        for err in r["errores"]:
            print(f"       {RED}→ {err}{RESET}")
        resp_preview = r["respuesta"][:120].replace("\n", "↵")
        print(f"       {DIM}resp: {resp_preview!r}{RESET}")
    elif verbose:
        resp_preview = r["respuesta"][:80].replace("\n", "↵")
        print(f"       {DIM}resp: {resp_preview!r}{RESET}")


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm",     action="store_true", help="Ejecutar grupo C (requiere APIs)")
    parser.add_argument("--grupo",   default="ABC",       help="Grupos a correr: A, B, C o combinación")
    parser.add_argument("--verbose", action="store_true", help="Mostrar respuesta incluso en tests ✅")
    args = parser.parse_args()

    grupos_activos = set(args.grupo.upper())
    usar_llm = args.llm or os.getenv("LLM_QA", "0") == "1"

    if "C" in grupos_activos and not usar_llm:
        print(f"{YELLOW}⚠  Grupo C requiere --llm para ejecutarse{RESET}")

    print(f"\n{BOLD}{'═' * 68}{RESET}")
    print(f"{BOLD}  Sercobot — Suite QA Crítica{RESET}")
    print(f"{BOLD}{'═' * 68}{RESET}")

    casos_a_correr = [c for c in CASOS if c.grupo in grupos_activos]
    resultados = []

    grupos_vistos = set()
    for caso in casos_a_correr:
        if caso.grupo not in grupos_vistos:
            label = {
                "A": "Routing puro (shortcut)",
                "B": "Contenido FAQ",
                "C": "Hechos críticos vía LLM",
            }.get(caso.grupo, caso.grupo)
            print(f"\n{BOLD}  Grupo {caso.grupo} — {label}{RESET}")
            print(f"  {'─' * 60}")
            grupos_vistos.add(caso.grupo)

        r = await ejecutar_caso(caso, usar_llm)
        imprimir_resultado(r, verbose=args.verbose)
        resultados.append(r)

    # ── Resumen ───────────────────────────────────────────────────────────────
    ejecutados = [r for r in resultados if not r.get("skip")]
    omitidos   = [r for r in resultados if r.get("skip")]
    pasados    = [r for r in ejecutados if not r["errores"]]
    fallidos   = [r for r in ejecutados if r["errores"]]
    regresiones_fallidas = [r for r in fallidos if r["caso"].regresion]

    print(f"\n{BOLD}{'═' * 68}{RESET}")
    print(f"{BOLD}  Resumen{RESET}")
    print(f"  {'─' * 60}")
    print(f"  Ejecutados:  {len(ejecutados)}   Omitidos: {len(omitidos)}")
    print(f"  {GREEN}Pasados:{RESET}    {len(pasados)}")
    print(f"  {RED if fallidos else GREEN}Fallidos:{RESET}   {len(fallidos)}")
    if regresiones_fallidas:
        print(f"  {RED}{BOLD}Anti-regresiones rotas: {len(regresiones_fallidas)}{RESET}")
        for r in regresiones_fallidas:
            print(f"    {RED}→ {r['caso'].regresion}{RESET}")

    tiempo_total = sum(r["elapsed_ms"] for r in ejecutados)
    print(f"  Tiempo total: {tiempo_total}ms")

    if fallidos:
        print(f"\n{BOLD}  Detalle de fallos:{RESET}")
        for r in fallidos:
            print(f"    [{r['caso'].grupo}] {r['caso'].mensaje!r}")
            for err in r["errores"]:
                print(f"         {RED}{err}{RESET}")

    print(f"\n{BOLD}{'═' * 68}{RESET}")
    verdict = f"{GREEN}{BOLD}✅ TODOS PASADOS{RESET}" if not fallidos else f"{RED}{BOLD}❌ HAY FALLOS{RESET}"
    print(f"  {verdict}")
    print(f"{BOLD}{'═' * 68}{RESET}\n")

    sys.exit(1 if fallidos else 0)


if __name__ == "__main__":
    asyncio.run(main())
