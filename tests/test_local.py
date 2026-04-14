# tests/test_local.py — Simulador de chat en terminal
# Generado por AgentKit

"""
Prueba a Emma sin necesitar WhatsApp.
Simula una conversación de ventas en la terminal.
"""

import asyncio
import sys
import os
import time
import logging

# Mostrar logs del agente durante el test
logging.basicConfig(
    level=logging.INFO,
    format="  %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)

# Agregar el directorio raíz al path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.brain import generar_respuesta
from agent.memory import inicializar_db, guardar_mensaje, obtener_historial, limpiar_historial

TELEFONO_TEST = "test-local-001"


async def main():
    """Loop principal del chat de prueba."""
    await inicializar_db()

    print()
    print("=" * 55)
    print("   WorldComputers — Emma (Test Local)")
    print("=" * 55)
    print()
    print("  Escribe mensajes como si fueras un cliente.")
    print("  Comandos especiales:")
    print("    'limpiar'  — borra el historial")
    print("    'salir'    — termina el test")
    print()
    print("-" * 55)
    print()

    while True:
        try:
            mensaje = input("Cliente: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nTest finalizado.")
            break

        if not mensaje:
            continue

        if mensaje.lower() == "salir":
            print("\nTest finalizado.")
            break

        if mensaje.lower() == "limpiar":
            await limpiar_historial(TELEFONO_TEST)
            print("[Historial borrado]\n")
            continue

        # Obtener historial ANTES de guardar (brain.py agrega el mensaje actual)
        historial = await obtener_historial(TELEFONO_TEST)

        # Generar respuesta de Emma
        print("\nEmma: ", end="", flush=True)
        t0 = time.monotonic()
        respuesta = await generar_respuesta(mensaje, historial)
        elapsed = time.monotonic() - t0
        print(respuesta)
        print(f"  ⏱  {elapsed:.1f}s\n")

        # Guardar mensaje del usuario y respuesta de Emma
        await guardar_mensaje(TELEFONO_TEST, "user", mensaje)
        await guardar_mensaje(TELEFONO_TEST, "assistant", respuesta)


if __name__ == "__main__":
    asyncio.run(main())
