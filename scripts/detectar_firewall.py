#!/usr/bin/env python3
"""
detectar_firewall.py — Diagnóstico de conectividad para SercoBot (SERCOP)

Prueba todas las conexiones de red que necesita el sistema y genera
un reporte listo para solicitar habilitación al equipo de Seguridad/Redes.

Uso:
    python scripts/detectar_firewall.py
    python scripts/detectar_firewall.py --salida reporte_firewall.txt
"""

import socket
import subprocess
import sys
import json
import argparse
import os
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field

# ── Intentar importar httpx (opcional, para pruebas HTTPS completas) ──────────
try:
    import httpx
    HTTPX_DISPONIBLE = True
except ImportError:
    HTTPX_DISPONIBLE = False

# ── Colores ANSI para terminal ────────────────────────────────────────────────
VERDE = "\033[92m"
ROJO  = "\033[91m"
AMARILLO = "\033[93m"
AZUL  = "\033[94m"
GRIS  = "\033[90m"
RESET = "\033[0m"
NEGRITA = "\033[1m"

def color(texto: str, c: str, sin_color: bool = False) -> str:
    return texto if sin_color else f"{c}{texto}{RESET}"


# ── Estructuras de datos ───────────────────────────────────────────────────────
@dataclass
class Prueba:
    categoria: str
    nombre: str
    protocolo: str
    host: str
    puerto: int
    descripcion: str
    critico: bool = True            # si falla, el sistema NO puede arrancar
    url_https: Optional[str] = None  # para pruebas HTTP/HTTPS adicionales

@dataclass
class Resultado:
    prueba: Prueba
    tcp_ok: bool
    http_ok: Optional[bool] = None
    http_codigo: Optional[int] = None
    latencia_ms: Optional[float] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        if self.prueba.url_https:
            return self.tcp_ok and (self.http_ok is True)
        return self.tcp_ok


# ── Definición de pruebas ─────────────────────────────────────────────────────
PRUEBAS: list[Prueba] = [

    # ── Servicios locales ──────────────────────────────────────────────────────
    Prueba(
        categoria="LOCAL",
        nombre="Ollama (LLM + embeddings)",
        protocolo="HTTP",
        host="localhost",
        puerto=11434,
        descripcion="Motor de IA local — gemma4:e2b y nomic-embed-text",
        critico=True,
        url_https="http://localhost:11434/api/tags",
    ),
    Prueba(
        categoria="LOCAL",
        nombre="PostgreSQL (base de conocimiento)",
        protocolo="TCP",
        host="localhost",
        puerto=5432,
        descripcion="Base de datos con pgvector — chunks normativos SERCOP",
        critico=True,
    ),
    Prueba(
        categoria="LOCAL",
        nombre="FastAPI (servidor del bot)",
        protocolo="HTTP",
        host="localhost",
        puerto=8000,
        descripcion="Servidor principal de SercoBot — webhook WhatsApp",
        critico=False,  # puede no estar corriendo al momento del diagnóstico
        url_https="http://localhost:8000/",
    ),

    # ── Groq API (LLM externo) ─────────────────────────────────────────────────
    Prueba(
        categoria="GROQ API",
        nombre="api.groq.com — HTTPS",
        protocolo="HTTPS",
        host="api.groq.com",
        puerto=443,
        descripcion="API de Groq para generación de respuestas con Llama 4 Scout 17B",
        critico=True,
        url_https="https://api.groq.com/",
    ),
    Prueba(
        categoria="GROQ API",
        nombre="console.groq.com — monitoreo",
        protocolo="HTTPS",
        host="console.groq.com",
        puerto=443,
        descripcion="Consola de Groq para monitoreo de uso y límites",
        critico=False,
        url_https="https://console.groq.com/",
    ),

    # ── Cloudflare Tunnel (webhook receptor) ───────────────────────────────────
    Prueba(
        categoria="CLOUDFLARE TUNNEL",
        nombre="cloudflare.com — HTTPS",
        protocolo="HTTPS",
        host="www.cloudflare.com",
        puerto=443,
        descripcion="Infraestructura principal de Cloudflare para el túnel",
        critico=True,
        url_https="https://www.cloudflare.com/",
    ),
    Prueba(
        categoria="CLOUDFLARE TUNNEL",
        nombre="trycloudflare.com — túnel rápido",
        protocolo="HTTPS",
        host="trycloudflare.com",
        puerto=443,
        descripcion="Dominio de tunnels rápidos de Cloudflare (webhook URL)",
        critico=True,
    ),
    Prueba(
        categoria="CLOUDFLARE TUNNEL",
        nombre="cloudflareaccess.com — acceso",
        protocolo="HTTPS",
        host="cloudflareaccess.com",
        puerto=443,
        descripcion="Cloudflare Access — autenticación del túnel",
        critico=True,
    ),
    Prueba(
        categoria="CLOUDFLARE TUNNEL",
        nombre="Puerto 7844 TCP (canal de datos)",
        protocolo="TCP",
        host="region1.v2.argotunnel.com",
        puerto=7844,
        descripcion="Puerto de datos del túnel Cloudflare (fallback a 443 si está bloqueado)",
        critico=False,
    ),

    # ── Meta WhatsApp API ──────────────────────────────────────────────────────
    Prueba(
        categoria="META WHATSAPP",
        nombre="graph.facebook.com — HTTPS",
        protocolo="HTTPS",
        host="graph.facebook.com",
        puerto=443,
        descripcion="API de Meta para enviar/recibir mensajes de WhatsApp Business",
        critico=True,
        url_https="https://graph.facebook.com/",
    ),
    Prueba(
        categoria="META WHATSAPP",
        nombre="graph.facebook.com — DNS",
        protocolo="DNS",
        host="graph.facebook.com",
        puerto=443,
        descripcion="Resolución DNS del servidor de Meta (confirma acceso a Internet)",
        critico=True,
    ),

    # ── SERCOP / Gobierno ──────────────────────────────────────────────────────
    Prueba(
        categoria="SERCOP PORTAL",
        nombre="compraspublicas.gob.ec",
        protocolo="HTTPS",
        host="portal.compraspublicas.gob.ec",
        puerto=443,
        descripcion="Portal de compras públicas — descarga de normativa y PDFs",
        critico=False,
        url_https="https://portal.compraspublicas.gob.ec/sercop/",
    ),
    Prueba(
        categoria="SERCOP PORTAL",
        nombre="sercop.gob.ec",
        protocolo="HTTPS",
        host="www.sercop.gob.ec",
        puerto=443,
        descripcion="Sitio institucional SERCOP — fuente de documentos normativos",
        critico=False,
        url_https="https://www.sercop.gob.ec/",
    ),

    # ── Salida a Internet general ──────────────────────────────────────────────
    Prueba(
        categoria="INTERNET",
        nombre="DNS público (8.8.8.8)",
        protocolo="TCP",
        host="8.8.8.8",
        puerto=53,
        descripcion="Resolución DNS externa — necesaria para Meta API y descarga de PDFs",
        critico=True,
    ),
    Prueba(
        categoria="INTERNET",
        nombre="PyPI (instalar dependencias Python)",
        protocolo="HTTPS",
        host="pypi.org",
        puerto=443,
        descripcion="Repositorio de paquetes Python — solo necesario durante instalación",
        critico=False,
        url_https="https://pypi.org/simple/",
    ),
    Prueba(
        categoria="INTERNET",
        nombre="Docker Hub (imágenes de contenedor)",
        protocolo="HTTPS",
        host="registry-1.docker.io",
        puerto=443,
        descripcion="Hub de imágenes Docker — solo necesario para instalar/actualizar",
        critico=False,
    ),
    Prueba(
        categoria="INTERNET",
        nombre="Hugging Face (modelos cross-encoder)",
        protocolo="HTTPS",
        host="huggingface.co",
        puerto=443,
        descripcion="Descarga del modelo de reranking — solo primera vez",
        critico=False,
        url_https="https://huggingface.co/",
    ),
    Prueba(
        categoria="INTERNET",
        nombre="Ollama Download (modelos LLM)",
        protocolo="HTTPS",
        host="registry.ollama.ai",
        puerto=443,
        descripcion="Descarga de modelos gemma4:e2b y nomic-embed-text — solo instalación inicial",
        critico=False,
    ),
]


# ── Funciones de prueba ───────────────────────────────────────────────────────

def probar_tcp(host: str, puerto: int, timeout: float = 5.0) -> tuple[bool, Optional[float], Optional[str]]:
    """Prueba conexión TCP/IP básica. Retorna (éxito, latencia_ms, error)."""
    import time
    try:
        inicio = time.monotonic()
        with socket.create_connection((host, puerto), timeout=timeout):
            latencia = (time.monotonic() - inicio) * 1000
            return True, round(latencia, 1), None
    except socket.timeout:
        return False, None, "TIMEOUT — firewall descartando paquetes (sin respuesta)"
    except ConnectionRefusedError:
        return False, None, "RECHAZADO — el puerto está cerrado en el destino (llega pero no escucha)"
    except socket.gaierror as e:
        return False, None, f"DNS FALLO — no resuelve el hostname: {e}"
    except OSError as e:
        return False, None, f"ERROR OS: {e}"


def probar_https(url: str, timeout: float = 8.0) -> tuple[Optional[bool], Optional[int], Optional[str]]:
    """Prueba HTTP/HTTPS con httpx. Retorna (éxito, código_http, error)."""
    if not HTTPX_DISPONIBLE:
        return None, None, "httpx no instalado — solo se hizo prueba TCP"
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, verify=False) as client:
            resp = client.get(url)
            ok = resp.status_code < 500
            return ok, resp.status_code, None
    except httpx.ConnectTimeout:
        return False, None, "TIMEOUT HTTPS — firewall bloqueando"
    except httpx.ConnectError as e:
        return False, None, f"CONEXIÓN RECHAZADA: {e}"
    except Exception as e:
        return False, None, f"Error HTTPS: {e}"


def ejecutar_prueba(p: Prueba) -> Resultado:
    """Ejecuta TCP + HTTPS (si aplica) para una prueba."""
    tcp_ok, latencia, error_tcp = probar_tcp(p.host, p.puerto)

    r = Resultado(prueba=p, tcp_ok=tcp_ok, latencia_ms=latencia, error=error_tcp)

    if tcp_ok and p.url_https:
        http_ok, codigo, error_http = probar_https(p.url_https)
        r.http_ok = http_ok
        r.http_codigo = codigo
        if error_http and not error_tcp:
            r.error = error_http

    return r


# ── Formateo del reporte ──────────────────────────────────────────────────────

def icono(resultado: Resultado, sin_color: bool) -> str:
    if resultado.ok:
        return color("✅ ABIERTO   ", VERDE, sin_color)
    elif resultado.tcp_ok and resultado.http_ok is False:
        return color("⚠️  PARCIAL   ", AMARILLO, sin_color)
    else:
        return color("❌ BLOQUEADO ", ROJO, sin_color)


def formatear_error(r: Resultado) -> str:
    if not r.error:
        return ""
    # Simplificar mensajes para el reporte
    e = r.error
    if "TIMEOUT" in e:
        return "→ El firewall descarta silenciosamente los paquetes (DROP)"
    if "RECHAZADO" in e:
        return "→ El paquete llega pero el servicio no está activo en ese puerto"
    if "DNS" in e.upper():
        return "→ El servidor DNS institucional no resuelve este dominio externo"
    return f"→ {e}"


def imprimir_reporte(resultados: list[Resultado], sin_color: bool = False) -> str:
    lineas = []
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    hostname = socket.gethostname()

    separador = "═" * 72

    lineas.append(f"\n{color(separador, AZUL, sin_color)}")
    lineas.append(f"{color('  DIAGNÓSTICO DE FIREWALL — SercoBot (SERCOP)', NEGRITA, sin_color)}")
    lineas.append(f"  Equipo: {hostname}   Fecha: {ahora}")
    lineas.append(f"{color(separador, AZUL, sin_color)}\n")

    # ── Por categoría ────────────────────────────────────────────────────────
    categorias = {}
    for r in resultados:
        categorias.setdefault(r.prueba.categoria, []).append(r)

    for cat, items in categorias.items():
        lineas.append(color(f"  [{cat}]", AZUL, sin_color))
        for r in items:
            latencia_str = f"{r.latencia_ms:.0f}ms" if r.latencia_ms else "—"
            http_str = f"  HTTP {r.http_codigo}" if r.http_codigo else ""
            linea = (
                f"  {icono(r, sin_color)}  "
                f"{r.prueba.nombre:<42} "
                f"{r.prueba.host}:{r.prueba.puerto:<6} "
                f"{latencia_str:>8}{http_str}"
            )
            lineas.append(linea)
            if r.error:
                lineas.append(f"            {color(formatear_error(r), GRIS, sin_color)}")
        lineas.append("")

    # ── Resumen ───────────────────────────────────────────────────────────────
    total      = len(resultados)
    abiertos   = sum(1 for r in resultados if r.ok)
    bloqueados = sum(1 for r in resultados if not r.ok)
    criticos_bloqueados = [r for r in resultados if not r.ok and r.prueba.critico]

    lineas.append(color(f"  {'─'*68}", AZUL, sin_color))
    lineas.append(f"  Resultado:  {color(str(abiertos), VERDE, sin_color)} abiertos   "
                  f"{color(str(bloqueados), ROJO, sin_color)} bloqueados   de {total} pruebas")

    if criticos_bloqueados:
        lineas.append(f"\n  {color('CRÍTICO — El sistema NO puede funcionar sin estas conexiones:', ROJO, sin_color)}")
        for r in criticos_bloqueados:
            lineas.append(f"    • {r.prueba.nombre}  ({r.prueba.host}:{r.prueba.puerto})")

    # ── Lista de habilitación solicitada ──────────────────────────────────────
    bloqueados_todos = [r for r in resultados if not r.ok]
    if bloqueados_todos:
        lineas.append(f"\n{color(separador, AZUL, sin_color)}")
        lineas.append(color("  SOLICITUD DE HABILITACIÓN DE FIREWALL", NEGRITA, sin_color))
        lineas.append(f"  Sistema: SercoBot — Asistente normativo SERCOP")
        lineas.append(f"  Equipo:  {hostname}")
        lineas.append(f"  Fecha:   {ahora}")
        lineas.append(f"{color(separador, AZUL, sin_color)}\n")
        lineas.append("  Se solicita habilitar las siguientes reglas de firewall:")
        lineas.append(f"  {'─'*68}")
        lineas.append(f"  {'#':<4} {'DIRECCIÓN':<8} {'PROTOCOLO':<10} {'DESTINO':<38} {'PUERTO':<7} {'USO'}")
        lineas.append(f"  {'─'*68}")

        for i, r in enumerate(bloqueados_todos, 1):
            p = r.prueba
            direccion = "LOCAL" if p.host in ("localhost", "127.0.0.1") else "SALIDA"
            lineas.append(
                f"  {i:<4} {direccion:<8} {p.protocolo:<10} {p.host:<38} {p.puerto:<7} {p.descripcion}"
            )

        lineas.append(f"  {'─'*68}")
        lineas.append(f"\n  Justificación técnica:")
        lineas.append("  Las conexiones marcadas SALIDA son necesarias para que el bot reciba")
        lineas.append("  y envíe mensajes de WhatsApp Business a través de Meta Cloud API.")
        lineas.append("  Las conexiones locales son entre servicios en este mismo equipo (Ollama,")
        lineas.append("  PostgreSQL) y no salen de la red institucional.")

    lineas.append(f"\n{color(separador, AZUL, sin_color)}\n")
    return "\n".join(lineas)


def exportar_json(resultados: list[Resultado]) -> dict:
    """Exporta resultados en formato JSON para sistemas de ticketing."""
    return {
        "equipo": socket.gethostname(),
        "fecha": datetime.now().isoformat(),
        "sistema": "SercoBot — SERCOP",
        "pruebas": [
            {
                "categoria": r.prueba.categoria,
                "nombre": r.prueba.nombre,
                "host": r.prueba.host,
                "puerto": r.prueba.puerto,
                "protocolo": r.prueba.protocolo,
                "estado": "ABIERTO" if r.ok else ("PARCIAL" if r.tcp_ok else "BLOQUEADO"),
                "critico": r.prueba.critico,
                "latencia_ms": r.latencia_ms,
                "http_codigo": r.http_codigo,
                "error": r.error,
                "descripcion": r.prueba.descripcion,
            }
            for r in resultados
        ],
        "resumen": {
            "total": len(resultados),
            "abiertos": sum(1 for r in resultados if r.ok),
            "bloqueados": sum(1 for r in resultados if not r.ok),
            "criticos_bloqueados": [
                f"{r.prueba.host}:{r.prueba.puerto}"
                for r in resultados if not r.ok and r.prueba.critico
            ],
        },
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Detecta restricciones de firewall para SercoBot (SERCOP)"
    )
    parser.add_argument(
        "--salida", "-o",
        metavar="ARCHIVO",
        help="Guardar reporte en archivo de texto (ej: reporte_firewall.txt)",
    )
    parser.add_argument(
        "--json",
        metavar="ARCHIVO",
        help="Guardar resultados en JSON (ej: firewall.json)",
    )
    parser.add_argument(
        "--sin-color",
        action="store_true",
        help="Deshabilitar colores ANSI (útil para archivos de texto o CI)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Timeout en segundos por prueba (default: 5)",
    )
    args = parser.parse_args()

    sin_color = args.sin_color or bool(args.salida)

    print(color("\n  Iniciando diagnóstico de conectividad...\n", AZUL))

    resultados = []
    for p in PRUEBAS:
        indicador = "  Probando..." if sin_color else f"  {color('●', AZUL)}  {p.nombre}..."
        print(indicador, end="\r", flush=True)
        r = ejecutar_prueba(p)
        resultados.append(r)
        est = color("✅", VERDE, sin_color) if r.ok else color("❌", ROJO, sin_color)
        print(f"  {est}  {p.nombre:<50}", flush=True)

    reporte = imprimir_reporte(resultados, sin_color=sin_color)
    print(reporte)

    if args.salida:
        with open(args.salida, "w", encoding="utf-8") as f:
            f.write(reporte)
        print(f"  Reporte guardado en: {args.salida}\n")

    if args.json:
        datos = exportar_json(resultados)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
        print(f"  JSON guardado en: {args.json}\n")

    # Código de salida: 0 si todo OK, 1 si hay críticos bloqueados
    criticos_fallidos = any(not r.ok and r.prueba.critico for r in resultados)
    sys.exit(1 if criticos_fallidos else 0)


if __name__ == "__main__":
    main()
