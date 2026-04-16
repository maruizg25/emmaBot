#!/usr/bin/env python3
"""
Fase 7 — Verificación completa de Sercobot.
Prueba los 10 mensajes especificados + simulación de Groq caído.

Uso:
    cd whatsapp-agentkit
    python tests/test_sercobot.py
"""

import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv
load_dotenv()

# Colores ANSI
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"


async def probar_mensaje(mensaje: str, esperado: str) -> dict:
    from agent.brain import generar_respuesta, _detectar_shortcut

    shortcut = _detectar_shortcut(mensaje)
    t0 = time.time()
    respuesta = await generar_respuesta(mensaje, [], telefono="test")
    elapsed_ms = int((time.time() - t0) * 1000)

    if shortcut:
        categoria, _ = shortcut
        proveedor = f"shortcut:{categoria}"
    else:
        proveedor = "llm_cascade"

    return {
        "mensaje":  mensaje,
        "esperado": esperado,
        "categoria": proveedor,
        "tiempo_ms": elapsed_ms,
        "respuesta": respuesta,
    }


def imprimir_resultado(r: dict):
    print(f"\n{BOLD}{'─' * 65}{RESET}")
    print(f"{BOLD}Mensaje:{RESET}   {r['mensaje']}")
    print(f"{BOLD}Esperado:{RESET}  {r['esperado']}")
    print(f"{BOLD}Detectado:{RESET} {CYAN}{r['categoria']}{RESET}")
    print(f"{BOLD}Tiempo:{RESET}    {r['tiempo_ms']} ms")
    respuesta_corta = r["respuesta"][:200].replace("\n", "↵ ")
    print(f"{BOLD}Respuesta:{RESET} {GREEN}{respuesta_corta}...{RESET}"
          if len(r["respuesta"]) > 200 else
          f"{BOLD}Respuesta:{RESET} {GREEN}{r['respuesta']}{RESET}")


async def main():
    print(f"\n{BOLD}{'═' * 65}{RESET}")
    print(f"{BOLD}  Sercobot — Verificación Fase 7{RESET}")
    print(f"{BOLD}{'═' * 65}{RESET}")

    casos = [
        ("hola",                                    "shortcut: saludo"),
        ("gracias",                                 "shortcut: agradecimiento"),
        ("qué película me recomiendas",             "shortcut: fuera de scope"),
        ("qué es el RUP",                           "FAQ cache hit"),
        ("cuáles son los requisitos para una SIE",  "RAG + Groq"),
        ("ok",                                      "shortcut: afirmación"),
        ("adiós",                                   "shortcut: despedida"),
        ("cuánto cuesta inscribirse en el RUP",     "FAQ cache hit"),
        ("tengo $8000 para contratar limpieza",     "RAG + cascada"),
        ("😊",                                      "shortcut: emoji"),
    ]

    resultados = []
    for mensaje, esperado in casos:
        r = await probar_mensaje(mensaje, esperado)
        imprimir_resultado(r)
        resultados.append(r)

    # ── Resumen ──────────────────────────────────────────────────────────────
    shortcuts = [r for r in resultados if "shortcut" in r["categoria"]]
    faq_hits  = [r for r in resultados if "faq_cache" in r["categoria"]]
    llm_hits  = [r for r in resultados if "llm" in r["categoria"] or "groq" in r["categoria"]]

    print(f"\n{BOLD}{'═' * 65}{RESET}")
    print(f"{BOLD}  Resumen de pruebas{RESET}")
    print(f"{'─' * 65}")
    print(f"  Total pruebas:    {len(resultados)}")
    print(f"  Shortcuts:        {len(shortcuts)}")
    print(f"  FAQ cache hits:   {len(faq_hits)}")
    print(f"  LLM cascade:      {len(llm_hits)}")
    print(f"  Tiempo promedio:  {int(sum(r['tiempo_ms'] for r in resultados) / len(resultados))} ms")

    # ── Simulación Groq caído ─────────────────────────────────────────────────
    print(f"\n{BOLD}{'─' * 65}{RESET}")
    print(f"{BOLD}  Simulación: Groq caído (timeout forzado){RESET}")
    print(f"{'─' * 65}")

    import agent.brain as brain
    groq_original = brain._PROVEEDORES.get("groq")

    async def _groq_timeout(_mensajes):
        raise asyncio.TimeoutError("simulando Groq caído")

    brain._PROVEEDORES["groq"] = (_groq_timeout, 0.1)

    for mensaje_test in [
        "cuáles son los requisitos para una SIE",
        "tengo $8000 para contratar limpieza",
    ]:
        r = await probar_mensaje(mensaje_test, "claude (Groq caído)")
        imprimir_resultado(r)

    # Restaurar Groq
    if groq_original:
        brain._PROVEEDORES["groq"] = groq_original

    print(f"\n{BOLD}{'═' * 65}{RESET}")
    print(f"{GREEN}{BOLD}  ✅ Verificación completa{RESET}")
    print(f"{BOLD}{'═' * 65}{RESET}\n")


if __name__ == "__main__":
    asyncio.run(main())
