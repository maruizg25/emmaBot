# agent/wiki.py — Compilador de wiki de conocimiento SERCOP

"""
Sistema de wiki compilada por LLM — Fase 2 de la arquitectura de conocimiento.

Inspirado en el enfoque de "LLM Knowledge Bases":
  raw/ documents → LLM compiles → knowledge/wiki/*.md → Q&A + fine-tuning data

El agente lee los chunks de la DB y genera artículos .md organizados por tema.
La wiki:
  - Tiene artículos temáticos (tipos de contratación, RUP, plazos, etc.)
  - Incluye backlinks entre artículos relacionados
  - Se auto-actualiza cuando llegan nuevos documentos
  - Sirve como contexto de largo plazo para el bot
  - Genera datos de entrenamiento para fine-tuning con Unsloth

Uso:
    python -m agent.wiki                    # compila wiki completa
    python -m agent.wiki --articulo "RUP"   # compila solo un artículo
    python -m agent.wiki --finetune         # exporta dataset Q&A para fine-tuning
"""

from __future__ import annotations

import os
import re
import json
import asyncio
import logging
from pathlib import Path
from datetime import datetime

import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

OLLAMA_URL   = os.getenv("OLLAMA_URL",   "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:e2b")
WIKI_DIR     = Path(os.getenv("WIKI_DIR", "knowledge/wiki"))

# Temas principales del wiki SERCOP
TEMAS_WIKI = [
    "Ley Orgánica del Sistema Nacional de Contratación Pública (LOSNCP) — Resumen",
    "Registro Único de Proveedores (RUP) — Requisitos y proceso",
    "Subasta Inversa Electrónica — Procedimiento completo",
    "Catálogo Electrónico — Cómo comprar y vender",
    "Menor Cuantía — Bienes, Servicios y Obras",
    "Cotización — Procedimiento y plazos",
    "Licitación — Proceso y requisitos",
    "Ínfima Cuantía — Límites y condiciones",
    "Contratación Directa — Casos y procedimiento",
    "Régimen Especial — Tipos y normativa",
    "Ferias Inclusivas — Economía Popular y Solidaria",
    "Plan Anual de Contratación (PAC) — Elaboración y publicación",
    "Portal ComprasPúblicas — Guía de uso para proveedores",
    "Portal ComprasPúblicas — Guía de uso para entidades",
    "Plazos y Términos de Contratación Pública",
    "Garantías en Contratación Pública",
    "Controversias y Recursos — Reclamos y apelaciones",
    "Transparencia y Acceso a la Información",
    "Preguntas Frecuentes Ciudadanos — SERCOP FAQ",
    "Índice General — Todos los temas SERCOP",
]


async def _llamar_gemma(prompt: str, system: str | None = None) -> str:
    """Llama a Gemma 4 via Ollama para generar contenido de wiki."""
    mensajes = []
    if system:
        mensajes.append({"role": "system", "content": system})
    mensajes.append({"role": "user", "content": prompt})

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={"model": OLLAMA_MODEL, "messages": mensajes, "stream": False},
            )
            response.raise_for_status()
            return response.json()["message"]["content"]
    except Exception as e:
        logger.error(f"Error llamando a Gemma para wiki: {e}")
        return ""


async def compilar_articulo(tema: str, contexto_chunks: str) -> str:
    """
    Genera un artículo wiki en Markdown sobre un tema específico
    usando los chunks de documentos como fuente de verdad.
    """
    system = """Eres un experto en contratación pública ecuatoriana compilando una wiki oficial del SERCOP.
Tu tarea: generar artículos de wiki claros, precisos y bien estructurados en Markdown.

Reglas:
- Usa solo información del CONTEXTO provisto. No inventes datos.
- Cita las fuentes: "(LOSNCP Art. X)", "(RGLOSNCP Art. Y)", "(Resolución No. Z)"
- Incluye backlinks a temas relacionados: [[Nombre del Tema]]
- Estructura con: # Título, ## Secciones, ### Subsecciones, listas, tablas cuando aplique
- Incluye al final una sección ## Referencias con las fuentes citadas
- Escribe para ciudadanos y proveedores — claro y accesible"""

    prompt = f"""Compila un artículo wiki sobre: **{tema}**

CONTEXTO (fragmentos de documentos oficiales SERCOP):
{contexto_chunks if contexto_chunks else "No hay contexto disponible aún. Genera una estructura base para este artículo."}

Genera el artículo wiki completo en Markdown. Incluye backlinks a temas relacionados."""

    return await _llamar_gemma(prompt, system)


async def compilar_indice(articulos: list[str]) -> str:
    """Genera el índice general de la wiki."""
    lista = "\n".join(f"- [[{a}]]" for a in articulos)
    prompt = f"""Genera un índice general de wiki para el SERCOP Ecuador.
Organiza estos artículos por categorías lógicas:

{lista}

Formato Markdown. Incluye una breve descripción de cada categoría."""
    return await _llamar_gemma(prompt)


async def exportar_dataset_finetune(output_path: str = "knowledge/finetune_dataset.jsonl") -> int:
    """
    Genera un dataset Q&A en formato JSONL para fine-tuning con Unsloth.

    Formato por línea:
    {"messages": [
        {"role": "system", "content": "..."},
        {"role": "user", "content": "pregunta"},
        {"role": "assistant", "content": "respuesta con cita"}
    ]}

    Returns: número de pares Q&A generados
    """
    from agent.memory import listar_documentos, buscar_chunks_fulltext

    wiki_path = WIKI_DIR
    if not wiki_path.exists():
        logger.warning("Wiki no compilada aún. Ejecuta wiki.py primero.")
        return 0

    system_sercop = (
        "Eres SercoBot, asistente virtual oficial del SERCOP Ecuador. "
        "Respondes preguntas sobre contratación pública citando siempre la normativa vigente."
    )

    pares = []
    for md_file in sorted(wiki_path.glob("*.md")):
        contenido = md_file.read_text(encoding="utf-8")
        tema = md_file.stem.replace("_", " ")

        prompt_gen = f"""Basándote en este artículo wiki del SERCOP, genera 5 pares pregunta-respuesta
en formato JSON array. Cada par debe ser:
{{"pregunta": "...", "respuesta": "... (Fuente: LOSNCP Art. X)"}}

Las preguntas deben ser las que haría un ciudadano o proveedor ecuatoriano.
Las respuestas deben citar la fuente específica.

ARTÍCULO WIKI:
{contenido[:3000]}

Devuelve SOLO el JSON array, sin texto adicional."""

        respuesta = await _llamar_gemma(prompt_gen)

        # Extraer JSON del response
        try:
            match = re.search(r"\[.*\]", respuesta, re.DOTALL)
            if match:
                qa_pairs = json.loads(match.group())
                for qa in qa_pairs:
                    if isinstance(qa, dict) and "pregunta" in qa and "respuesta" in qa:
                        pares.append({
                            "messages": [
                                {"role": "system", "content": system_sercop},
                                {"role": "user",   "content": qa["pregunta"]},
                                {"role": "assistant", "content": qa["respuesta"]},
                            ]
                        })
        except json.JSONDecodeError:
            logger.warning(f"No se pudo parsear Q&A para {tema}")

    # Guardar dataset
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for par in pares:
            f.write(json.dumps(par, ensure_ascii=False) + "\n")

    logger.info(f"Dataset exportado: {len(pares)} pares Q&A → {output_path}")
    return len(pares)


async def compilar_wiki_completa():
    """Compila todos los artículos de la wiki usando los documentos ingestados."""
    WIKI_DIR.mkdir(parents=True, exist_ok=True)

    from agent.retriever import buscar_contexto, formatear_contexto

    articulos_generados = []
    for tema in TEMAS_WIKI:
        logger.info(f"Compilando: {tema}")
        chunks = await buscar_contexto(tema)
        contexto = formatear_contexto(chunks)

        articulo = await compilar_articulo(tema, contexto)
        if not articulo:
            continue

        # Nombre de archivo: tema en snake_case
        nombre_archivo = re.sub(r"[^a-z0-9]+", "_", tema.lower()).strip("_")
        ruta = WIKI_DIR / f"{nombre_archivo}.md"

        # Agregar metadata al inicio del artículo
        header = f"---\ntitulo: {tema}\nfecha_compilacion: {datetime.utcnow().isoformat()}\n---\n\n"
        ruta.write_text(header + articulo, encoding="utf-8")

        articulos_generados.append(tema)
        logger.info(f"  → Guardado: {ruta}")

    # Generar índice
    if articulos_generados:
        indice = await compilar_indice(articulos_generados)
        (WIKI_DIR / "00_indice.md").write_text(indice, encoding="utf-8")
        logger.info(f"Wiki compilada: {len(articulos_generados)} artículos")

    return articulos_generados


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    if "--finetune" in sys.argv:
        asyncio.run(exportar_dataset_finetune())
    elif "--articulo" in sys.argv:
        idx = sys.argv.index("--articulo")
        tema = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else TEMAS_WIKI[0]
        async def _single():
            from agent.retriever import buscar_contexto, formatear_contexto
            chunks = await buscar_contexto(tema)
            contexto = formatear_contexto(chunks)
            articulo = await compilar_articulo(tema, contexto)
            print(articulo)
        asyncio.run(_single())
    else:
        asyncio.run(compilar_wiki_completa())
