# CLAUDE.md — AgentKit SERCOP Edition

> Contexto maestro del proyecto. Claude Code lo lee automáticamente en cada sesión.
> Refleja el estado REAL del código base (AgentKit) y la evolución hacia el bot
> de conocimiento normativo del SERCOP.
>
> Última actualización: 9 Abril 2026 (rev. 2)

---

## 1. Qué es este proyecto

**AgentKit** es un sistema base funcional para construir agentes de WhatsApp con IA.
Este repositorio es una adaptación específica para el **SERCOP** (Servicio Nacional de
Contratación Pública, Ecuador): un bot que responde preguntas sobre normativa de
contratación pública citando artículos y resoluciones fuente.

### Audiencia objetivo

SercoBot atiende a **ciudadanos y proveedores del Estado** — no solo a funcionarios del SERCOP.
Las preguntas típicas vienen de personas que quieren participar en contratación pública,
registrarse como proveedores, entender un proceso o saber qué documentos necesitan.

### Dos capas del proyecto

| Capa | Estado | Descripción |
|---|---|---|
| **AgentKit Core** | ✅ Funciona | FastAPI + WhatsApp multi-provider + memoria PostgreSQL |
| **RAG SERCOP** | ✅ Funciona | pgvector + Gemma 4 local + pipeline híbrido + reranker + tool calling |

**Principio clave:** No romper lo que funciona. La capa de WhatsApp (providers/, main.py,
memory.py) se mantiene intacta. El RAG y las tools se integran dentro de brain.py.

---

## 2. Arquitectura actual (AgentKit base — funciona hoy)

```
agentkit/
├── agent/
│   ├── __init__.py
│   ├── main.py            ← FastAPI + webhook (provider-agnostic)
│   ├── brain.py           ← Claude API + system prompt desde prompts.yaml
│   ├── memory.py          ← SQLAlchemy + SQLite, historial por teléfono
│   ├── tools.py           ← Herramientas del negocio
│   └── providers/
│       ├── __init__.py    ← Factory: obtener_proveedor()
│       ├── base.py        ← Clase abstracta ProveedorWhatsApp + MensajeEntrante
│       ├── whapi.py       ← Adaptador Whapi.cloud
│       ├── meta.py        ← Adaptador Meta Cloud API
│       └── twilio.py      ← Adaptador Twilio
├── config/
│   ├── business.yaml      ← Datos del negocio SERCOP
│   └── prompts.yaml       ← System prompt del agente
├── knowledge/             ← PDFs y docs del SERCOP (normativa)
├── tests/
│   └── test_local.py      ← Chat interactivo en terminal
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .env
```

### Flujo actual de un mensaje

```
WhatsApp (funcionario escribe)
    ↓
Proveedor (Whapi / Meta / Twilio) → POST /webhook
    ↓
providers/ → normaliza a MensajeEntrante
    ↓
main.py → obtener_historial() + generar_respuesta()
    ↓
brain.py → Claude API (claude-sonnet-4-6) con system prompt
    ↓
respuesta → providers/ → WhatsApp
```

---

## 3. Arquitectura objetivo (AgentKit + RAG SERCOP)

El cambio está en **brain.py**: antes de llamar a Claude/Gemma,
se hace retrieval en pgvector para enriquecer el contexto con normativa real.

```
WhatsApp (funcionario escribe)
    ↓
providers/ → MensajeEntrante (sin cambios)
    ↓
main.py (sin cambios)
    ↓
brain.py → [NUEVO] rag.py → pgvector + pgvectorscale
               ↓ chunks relevantes con citación
           Gemma 4 26B (Ollama local) con contexto normativo
    ↓
respuesta con artículo citado → WhatsApp
```

### Nuevo módulo RAG a agregar

```
agentkit/
├── agent/
│   ├── rag/                    ← NUEVO módulo
│   │   ├── __init__.py
│   │   ├── retriever.py        ← LlamaIndex + pgvector + hybrid search
│   │   ├── ingestor.py         ← Pipeline de ingesta de PDFs normativos
│   │   └── embedder.py         ← nomic-embed-text vía Ollama
│   └── brain.py                ← MODIFICADO: usa RAG si disponible, Claude API si no
├── knowledge/
│   ├── losncp/                 ← PDFs LOSNCP
│   ├── reglamento/             ← Reglamento General
│   └── resoluciones/           ← Resoluciones SERCOP
└── scripts/
    └── ingestar.py             ← Script CLI para indexar documentos
```

---

## 4. Stack tecnológico completo

### Capa WhatsApp (existente — NO modificar)

| Componente | Tecnología |
|---|---|
| Servidor | FastAPI + Uvicorn |
| Proveedores | Whapi.cloud / Meta Cloud API / Twilio |
| Memoria | SQLAlchemy + SQLite (dev) / PostgreSQL (prod) |
| Variables | python-dotenv |
| Deploy | Docker Compose + Railway |

### Capa RAG SERCOP (implementada)

| Componente | Tecnología | Estado |
|---|---|---|
| LLM producción Mac | **gemma4:e2b** (Ollama) | ✅ Activo — ~5s/respuesta |
| LLM producción RHEL | **gemma4:26b** (Ollama) | ⏳ Pendiente deploy — NO usar en Mac |
| Embeddings | nomic-embed-text 768d (Ollama) | ✅ Activo |
| Vector DB | PostgreSQL 16 + pgvector 0.8.2 | ✅ Activo (VM pg-db 192.168.2.2) |
| Búsqueda híbrida | pgvector HNSW coseno + GIN tsvector español | ✅ Activo |
| Fusión de rankings | Reciprocal Rank Fusion (RRF) top-12 | ✅ Activo |
| Reranking | cross-encoder/mmarco-mMiniLMv2-L12-H384-v1 | ✅ Activo — top-4 final |
| Tool calling | 5 tools JSON Schema via Ollama /api/chat | ✅ Activo |
| Evaluación | RAGAS | ⏳ Pendiente |

---

## 5. LLM: Gemma 4 (lanzado 2 abril 2026)

### Por qué Gemma 4 para SERCOP

**Restricción institucional:** ningún dato de contratación pública puede salir
de la infraestructura del SERCOP. Claude API (cloud) no puede usarse en producción.
Gemma 4 corre 100% local con Ollama.

### Variantes — cuál usar

| Modelo | Parámetros activos | RAM Q4 | Uso |
|---|---|---|---|
| gemma4:e4b | ~4.5B | ~5 GB | Prototipo rápido |
| **gemma4:26b** | ~4B activos (26B MoE) | ~18 GB | **Producción SERCOP** |
| gemma4:31b | 31B | ~20 GB | Si hay GPU disponible |

El 26B MoE activa solo ~4B parámetros por token — mismo VRAM que un modelo de 4B
pero con calidad de razonamiento de 26B.

### Benchmarks relevantes

| Benchmark | Gemma 3 27B | Gemma 4 26B MoE |
|---|---|---|
| τ2-bench (tool use) | 6.6% | ~86% |
| AIME 2026 (matemáticas) | 20.8% | 88.3% |
| GPQA Diamond (ciencias) | 42.4% | 82.3% |
| Arena AI ELO | 1365 | 1441 |

### Instalación

```bash
# Requiere Ollama v0.20+
ollama pull gemma4:26b
ollama pull nomic-embed-text

# Verificar
ollama run gemma4:26b "¿Qué es la contratación pública?"
```

---

## 6. Código — módulos existentes (NO tocar)

### `agent/providers/base.py`

```python
from dataclasses import dataclass
from fastapi import Request
from abc import ABC, abstractmethod

@dataclass
class MensajeEntrante:
    telefono: str
    texto: str
    mensaje_id: str
    es_propio: bool

class ProveedorWhatsApp(ABC):
    @abstractmethod
    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]: ...
    @abstractmethod
    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool: ...
    async def validar_webhook(self, request: Request) -> dict | int | None:
        return None
```

### `agent/main.py` — flujo principal (no modificar)

```python
# El webhook llama a:
mensajes = await proveedor.parsear_webhook(request)
historial = await obtener_historial(msg.telefono)
respuesta = await generar_respuesta(msg.texto, historial)  # ← brain.py
await guardar_mensaje(msg.telefono, "user", msg.texto)
await guardar_mensaje(msg.telefono, "assistant", respuesta)
await proveedor.enviar_mensaje(msg.telefono, respuesta)
```

### `agent/memory.py` — historial por teléfono (no modificar)

```python
# API pública del módulo:
await inicializar_db()
await guardar_mensaje(telefono, role, content)
await obtener_historial(telefono, limite=20) -> list[dict]
await limpiar_historial(telefono)
```

---

## 7. Código — brain.py modificado (integración RAG)

Este es el único archivo existente que se modifica. El cambio: si el RAG
está disponible, enriquece el contexto antes de llamar al LLM.

```python
# agent/brain.py — VERSIÓN SERCOP con RAG
import os
import yaml
import logging
from anthropic import AsyncAnthropic
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

# Modo de operación
RAG_ENABLED = os.getenv("RAG_ENABLED", "false").lower() == "true"
LLM_MODE = os.getenv("LLM_MODE", "claude")  # "claude" | "gemma4"

client_claude = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def cargar_system_prompt() -> str:
    try:
        with open("config/prompts.yaml", "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
            return config.get("system_prompt", "Eres un asistente útil.")
    except FileNotFoundError:
        return "Eres un asistente experto en contratación pública ecuatoriana."


async def generar_respuesta(mensaje: str, historial: list[dict]) -> str:
    """
    Genera respuesta. Si RAG está activo, enriquece con normativa antes de llamar al LLM.
    """
    system_prompt = cargar_system_prompt()
    contexto_rag = ""

    # Paso 1: RAG retrieval (si está habilitado)
    if RAG_ENABLED:
        try:
            from agent.rag.retriever import recuperar_contexto
            contexto_rag = await recuperar_contexto(mensaje)
            if contexto_rag:
                system_prompt += f"\n\n## Normativa relevante recuperada\n{contexto_rag}"
                logger.info(f"RAG: contexto agregado ({len(contexto_rag)} chars)")
        except Exception as e:
            logger.warning(f"RAG no disponible, usando sin contexto: {e}")

    # Paso 2: Llamar al LLM configurado
    mensajes = historial + [{"role": "user", "content": mensaje}]

    if LLM_MODE == "gemma4":
        return await _llamar_gemma4(system_prompt, mensajes)
    else:
        return await _llamar_claude(system_prompt, mensajes)


async def _llamar_claude(system_prompt: str, mensajes: list[dict]) -> str:
    """Llama a Claude API (desarrollo / fallback)."""
    try:
        response = await client_claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system_prompt,
            messages=mensajes,
        )
        return response.content[0].text
    except Exception as e:
        logger.error(f"Error Claude API: {e}")
        return "Lo siento, estoy teniendo problemas técnicos. Intenta de nuevo."


async def _llamar_gemma4(system_prompt: str, mensajes: list[dict]) -> str:
    """Llama a Gemma 4 local vía Ollama (producción SERCOP)."""
    import httpx
    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    model = os.getenv("LLM_MODEL", "gemma4:26b")

    # Construir prompt con system + historial
    prompt_completo = f"<s>\n{system_prompt}\n</s>\n\n"
    for msg in mensajes:
        role = "Usuario" if msg["role"] == "user" else "Asistente"
        prompt_completo += f"{role}: {msg['content']}\n"
    prompt_completo += "Asistente:"

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt_completo,
                    "stream": False,
                    "options": {"temperature": 0.1},
                },
            )
            data = response.json()
            return data.get("response", "").strip()
    except Exception as e:
        logger.error(f"Error Gemma 4 (Ollama): {e}")
        # Fallback a Claude si Ollama no responde
        return await _llamar_claude(system_prompt, mensajes)
```

---

## 8. Código — módulo RAG (nuevo)

### `agent/rag/retriever.py`

```python
# agent/rag/retriever.py — Retrieval de normativa SERCOP
import os
import logging
from llama_index.core import VectorStoreIndex, StorageContext
from llama_index.core.retrievers import AutoMergingRetriever
from llama_index.core.postprocessor import SentenceTransformerRerank
from llama_index.vector_stores.postgres import PGVectorStore
from llama_index.embeddings.ollama import OllamaEmbedding

logger = logging.getLogger("agentkit")
_query_engine = None  # Singleton


def _construir_query_engine():
    embed_model = OllamaEmbedding(
        model_name="nomic-embed-text",
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
    )
    vector_store = PGVectorStore.from_params(
        database=os.getenv("POSTGRES_DB", "sercop_bot"),
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5432)),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
        table_name="chunks_sercop",
        embed_dim=768,
        hybrid_search=True,
        text_search_config="spanish",
    )
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    index = VectorStoreIndex.from_vector_store(vector_store, embed_model=embed_model)
    base_retriever = index.as_retriever(
        similarity_top_k=6,
        vector_store_query_mode="hybrid",
        alpha=0.5,
    )
    retriever = AutoMergingRetriever(base_retriever, storage_context)
    reranker = SentenceTransformerRerank(
        model="cross-encoder/ms-marco-MiniLM-L-2-v2",
        top_n=3,
    )
    from llama_index.core.query_engine import RetrieverQueryEngine
    return RetrieverQueryEngine(retriever=retriever, node_postprocessors=[reranker])


async def recuperar_contexto(pregunta: str) -> str:
    """
    Recupera chunks de normativa relevante.
    Retorna texto con citaciones para incluir en el system prompt.
    """
    global _query_engine
    if _query_engine is None:
        _query_engine = _construir_query_engine()

    try:
        response = _query_engine.retrieve(pregunta)
        if not response:
            return ""
        partes = []
        for node in response:
            meta = node.metadata or {}
            fuente = meta.get("tipo", "normativa").upper()
            articulo = meta.get("articulo", "")
            ref = f"[{fuente}{' — ' + articulo if articulo else ''}]"
            partes.append(f"{ref}\n{node.text}")
        return "\n\n---\n\n".join(partes)
    except Exception as e:
        logger.error(f"Error en RAG retrieval: {e}")
        return ""
```

### `agent/rag/ingestor.py`

```python
# agent/rag/ingestor.py — Ingesta de documentos normativos
import os
import logging
from llama_index.core import SimpleDirectoryReader
from llama_index.core.node_parser import HierarchicalNodeParser, get_leaf_nodes
from llama_index.core.ingestion import IngestionPipeline
from llama_index.vector_stores.postgres import PGVectorStore
from llama_index.embeddings.ollama import OllamaEmbedding

logger = logging.getLogger("agentkit")


def ingestar_directorio(ruta: str, metadata: dict) -> int:
    """
    Ingesta todos los PDFs de un directorio al vector store.
    Retorna el número de chunks creados.
    """
    embed_model = OllamaEmbedding(
        model_name="nomic-embed-text",
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
    )
    vector_store = PGVectorStore.from_params(
        database=os.getenv("POSTGRES_DB"),
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5432)),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
        table_name="chunks_sercop",
        embed_dim=768,
        hybrid_search=True,
        text_search_config="spanish",
    )
    node_parser = HierarchicalNodeParser.from_defaults(chunk_sizes=[2048, 512, 128])
    documents = SimpleDirectoryReader(ruta).load_data()
    for doc in documents:
        doc.metadata.update(metadata)
    nodes = node_parser.get_nodes_from_documents(documents)
    leaf_nodes = get_leaf_nodes(nodes)
    pipeline = IngestionPipeline(transformations=[embed_model], vector_store=vector_store)
    pipeline.run(nodes=leaf_nodes)
    logger.info(f"Ingestados {len(leaf_nodes)} chunks desde {ruta}")
    return len(leaf_nodes)
```

---

## 9. Schema PostgreSQL (RAG)

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS vectorscale;

CREATE TABLE IF NOT EXISTS chunks_sercop (
    id          BIGSERIAL PRIMARY KEY,
    tipo        TEXT,        -- 'losncp' | 'reglamento' | 'resolucion'
    articulo    TEXT,
    seccion     TEXT,
    texto       TEXT NOT NULL,
    metadata    JSONB,
    embedding   vector(768),
    creado_en   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON chunks_sercop USING diskann (embedding);

CREATE INDEX IF NOT EXISTS idx_chunks_fts
    ON chunks_sercop USING gin(to_tsvector('spanish', texto));
```

---

## 10. Variables de entorno completas

```env
# ── WhatsApp (existente) ────────────────────────────────────
ANTHROPIC_API_KEY=sk-ant-...
WHATSAPP_PROVIDER=meta          # whapi | meta | twilio

META_ACCESS_TOKEN=...
META_PHONE_NUMBER_ID=...
META_VERIFY_TOKEN=sercop-verify

# ── Servidor ────────────────────────────────────────────────
PORT=8000
ENVIRONMENT=production

# ── Base de datos conversaciones (existente) ─────────────────
DATABASE_URL=sqlite+aiosqlite:///./agentkit.db

# ── Base de datos RAG (nuevo) ────────────────────────────────
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=sercop_bot
POSTGRES_USER=sercop_user
POSTGRES_PASSWORD=tu_password

# ── RAG + LLM local (nuevo) ──────────────────────────────────
RAG_ENABLED=true                # false en dev sin pgvector
LLM_MODE=gemma4                 # claude | gemma4
OLLAMA_BASE_URL=http://localhost:11434
LLM_MODEL=gemma4:26b
EMBED_MODEL=nomic-embed-text
```

---

## 11. System prompt SERCOP (`config/prompts.yaml`)

```yaml
system_prompt: |
  Eres el asistente virtual de contratación pública del SERCOP (Servicio Nacional
  de Contratación Pública del Ecuador).

  ## Tu identidad
  - Tu nombre es SERCOP Bot
  - Representas al SERCOP oficialmente
  - Hablas siempre en español formal

  ## Tu función
  Responder preguntas sobre normativa de contratación pública ecuatoriana:
  LOSNCP, Reglamento General, resoluciones y directrices del SERCOP.

  ## Reglas
  - Cita SIEMPRE el artículo, numeral o resolución fuente
  - Si no tienes la información: "No encontré esa información en la normativa
    disponible. Consulta directamente en sercop.gob.ec"
  - NUNCA inventes artículos, montos o plazos
  - Respuestas concisas pero completas

fallback_message: "Disculpa, no entendí tu consulta. ¿Podrías reformularla?"
error_message: "Estoy teniendo problemas técnicos. Por favor intenta en unos minutos."
```

---

## 12. Dependencias (`requirements.txt`)

```
# AgentKit base (existente)
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
anthropic>=0.40.0
httpx>=0.25.0
python-dotenv>=1.0.0
sqlalchemy>=2.0.0
pyyaml>=6.0.1
aiosqlite>=0.19.0
python-multipart>=0.0.6

# RAG SERCOP (nuevo)
llama-index>=0.10.0
llama-index-vector-stores-postgres
llama-index-embeddings-ollama
llama-index-llms-ollama
sentence-transformers
psycopg2-binary
ragas
```

---

## 13. Comandos de referencia

```bash
# Desarrollo (Claude API, sin RAG)
RAG_ENABLED=false LLM_MODE=claude uvicorn agent.main:app --reload --port 8000
python tests/test_local.py

# Producción local (Gemma 4 + RAG)
ollama serve
uvicorn agent.main:app --host 0.0.0.0 --port 8000

# Ingesta de documentos
python scripts/ingestar.py --dir knowledge/losncp --tipo losncp
python scripts/ingestar.py --dir knowledge/reglamento --tipo reglamento
python scripts/ingestar.py --dir knowledge/resoluciones --tipo resolucion

# Docker
docker compose up --build
docker compose logs -f agent
```

---

## 14. Estado actual del sistema (9 Abril 2026 rev. 2)

### Infraestructura PostgreSQL (Multipass)

| VM | IP | Puerto | Rol |
|---|---|---|---|
| pg-db | 192.168.2.2 | 5432 | PostgreSQL 16 + pgvector 0.8.2 — base sercop_db |
| pg-bouncer | 192.168.2.3 | 6432 | Connection pooler |
| pg-pool | 192.168.2.4 | 9999 | Pooler avanzado |

DBeaver conectado a sercop_db para monitoreo visual.

### Base de conocimiento

- **3,059 chunks** indexados en 17 documentos
- **91.5% de chunks** con metadata de artículo (`{"articulo": "Art. 74"}`)
  - Reglamento: 100% · Resoluciones: 97% · Manuales SOCE: 1% (normal, no citan artículos)
- Embeddings: `nomic-embed-text` 768d via Ollama
- Índices: HNSW coseno + GIN tsvector español

#### Documentos indexados (17)

| Documento | Tipo | Chunks |
|---|---|---|
| Reglamento General LOSNCP — octubre 2025 | reglamento | 1,132 |
| Normativa Secundaria de Contratación Pública 2025 | reglamento | 1,125 |
| LOSNCP — RO 140 07-X-2025 | ley | 113 |
| Manual SOCE — Subasta Inversa Electrónica | manual_soce | 108 |
| Manual SOCE — Fase Contractual Bienes y Servicios | manual_soce | 19 |
| RE-SERCOP-2026-0001 (Metodología de Control) | resolucion | 60 |
| RE-SERCOP-2026-0002 (Comité Interinstitucional) | resolucion | 42 |
| Modelo de Pliego SICAE | resolucion | 141 |
| Metodología de Control Final Sumillada | resolucion | 137 |
| Código de Ética institucional | resolucion | 89 |
| Norma Interna SICAE (fármacos y bienes estratégicos) | resolucion | 33 |
| Instructivo Extorsión | resolucion | 29 |
| Resolución Régimen de Transición — nov. 2025 | resolucion | 14 |
| RE-SERCOP-2024-0144 | resolucion | 5 |
| RE-SERCOP-2025-0152 | resolucion | 5 |
| Fe de Errata RE-SERCOP-2024-0142 | resolucion | 1 |
| Glosario de Términos SERCOP | manual | 6 |

### Pipeline RAG activo

```
query → expand (siglas SERCOP) → embed (nomic) → pgvector HNSW + tsvector
      → RRF (top-12) → cross-encoder reranker → top-4 chunks → Gemma → respuesta
```

### Tool calling activo (5 tools)

El agente decide solo cuándo usar cada tool vs. el RAG:

| Tool | Cuándo se activa | Datos que entrega |
|---|---|---|
| `obtener_montos_pie` | Pregunta por monto/umbral en USD de cualquier proceso | Valores exactos 2025/2026 por tipo |
| `recomendar_tipo_contratacion` | Pregunta qué proceso usar dado un bien/servicio/monto | Nombre, normativa y ventaja del proceso |
| `obtener_plazos` | Pregunta por días/plazos de proceso, impugnación, contrato o garantías | Plazos de 7 tipos: SIE, menor cuantía, cotización, licitación, impugnacion, contrato, garantias |
| `info_rup` | Pregunta sobre registro de proveedores | Requisitos, costo, tiempo, causales de suspensión |
| `obtener_fecha_hora_ecuador` | Pregunta por fecha/hora actual | Fecha, hora y día en Ecuador (UTC-5) |

**Regla de routing:** montos/umbrales → `obtener_montos_pie` (NO el RAG — los valores del RAG pueden estar desactualizados). Plazos e impugnaciones → `obtener_plazos`. Todo lo demás → RAG con citación.

### Modelos LLM

| Modelo | Estado | Uso |
|---|---|---|
| `gemma4:e2b` | ✅ Activo en Mac | **Producción actual** — fluido, ~5s/respuesta |
| `gemma4:26b` | ⚠️ Descargado — NO usar en Mac | Solo para servidor RHEL SERCOP con GPU |
| `nomic-embed-text` | ✅ Activo | Embeddings de 768 dimensiones |

**CRÍTICO:** `gemma4:26b` en Mac = 3-5 min/respuesta + crash del sistema. Configurado en `.env` como `OLLAMA_MODEL=gemma4:e2b`. No cambiar.

### SercoBot — prompt v3 activo (`config/prompts.yaml`)

- **Identidad:** asistente cálida, directa, "colega experta" — no robot institucional
- **Audiencia:** ciudadanos y proveedores, no solo funcionarios
- **Lógica de decisión:** saludo puro → menú · pregunta → RAG/tool · ambigua → pide un dato
- **Anti-alucinación:** chunk sin artículo explícito → "La normativa establece que [concepto], aunque te recomiendo verificar en compraspublicas.gob.ec"
- **Anti-filename:** NUNCA cita nombres de archivos PDF como fuente (ej. "Normativa Secundaria Actualizada 1")
- **Siglas reconocidas:** SIE, RUP, PAC, LOSNCP, RGLOSNCP, SOCE, SIC, SICAE, EPS, MIPYMES
- **UX WhatsApp:** acuse inmediato "🔍 Consultando..." · menú de bienvenida con 5 categorías · cierres variados
- **Deduplicación:** `_mensajes_procesados: set[str]` evita doble respuesta por reintentos de Meta

### Comandos para levantar el sistema

```bash
# 1. Verificar Ollama activo
curl http://localhost:11434/api/tags

# 2. Levantar servidor
source /Users/mauricioruiz/emmabot/.venv/bin/activate
cd /Users/mauricioruiz/emmabot/whatsapp-agentkit
uvicorn agent.main:app --host 0.0.0.0 --port 8000

# 3. Verificar ngrok activo
curl http://localhost:4040/api/tunnels

# Agregar documentos nuevos al RAG
python scripts/scraper_biblioteca.py      # descarga + ingesta desde portal SERCOP
python ingestar_knowledge.py              # re-ingesta knowledge/ local
```

---

## 14b. Pendiente para producción completa

### Documentos faltantes (descargar manualmente y ejecutar scraper)

| Documento | Por qué importa | Acción |
|---|---|---|
| Manual SOCE — Menor Cuantía bienes/servicios | Proceso más común en contratación pública | Descargar de biblioteca SERCOP → `knowledge/biblioteca/` |
| Manual SOCE — Menor Cuantía obras | Segundo proceso más común | Ídem |
| Manual SOCE — Registro de Proveedores | Alta demanda de ciudadanos que quieren ser proveedores | Ídem |
| Manual SOCE — Contrataciones de Emergencia | Preguntas frecuentes en situaciones urgentes | Ídem |
| Manual SOCE — Feria Inclusiva | EPS y MIPYMES — audiencia clave | Ídem |
| COA (Código Orgánico Administrativo) | Capítulos de recursos/impugnaciones formales | Buscar en lexis.com.ec o registroficial.gob.ec |
| Resolución montos PIE 2026 | Fuente citable oficial para los umbrales en USD | Buscar en compraspublicas.gob.ec/cat_normativas |

Luego de descargar, ejecutar: `python scripts/scraper_biblioteca.py` (ya detecta archivos existentes y no los re-ingesta).

### Tareas técnicas

- [ ] Evaluación RAGAS: correr suite con 20 preguntas · meta faithfulness > 0.85
- [ ] Deploy en servidor RHEL: migrar sercop_db + instalar Ollama + activar gemma4:26b
- [ ] Demo SERCOP: 5-7 preguntas killer para presentación al coordinador TIC

---

## 14c. NO TOCAR

- `providers/` — capa WhatsApp estable, 3 providers funcionando
- Schema PostgreSQL — tablas `documentos`, `chunks`, `mensajes`
- Los 3,059 chunks indexados en sercop_db
- `config/prompts.yaml` — prompt v3 optimizado y probado con 5/5 tests
- `.env` — `OLLAMA_MODEL=gemma4:e2b` — NO cambiar a 26b en Mac

---

## 15. Roadmap

| Fase | Estado | Descripción |
|---|---|---|
| AgentKit base | ✅ Completo | FastAPI + WhatsApp + memoria |
| RAG + pgvector | ✅ Completo | Pipeline híbrido + reranker + 3,059 chunks |
| SercoBot prompt v3 | ✅ Completo | Anti-alucinación + menú + siglas + UX WhatsApp |
| Tool calling nativo | ✅ Completo | 5 tools: montos PIE, tipos, plazos, RUP, fecha |
| Base de conocimiento v2 | ✅ Completo | +7 documentos nuevos, incluyendo Reglamento oct 2025 |
| Documentos faltantes | 🔧 En curso | 5 manuales SOCE + COA + PIE 2026 |
| Evaluación RAGAS | ⏳ Pendiente | faithfulness > 0.85 |
| Deploy servidor RHEL | ⏳ Pendiente | gemma4:26b + sercop_db en infraestructura SERCOP |

---

## 16. Decisiones de arquitectura

**¿Por qué no reescribir AgentKit?**
La capa de WhatsApp funciona con 3 proveedores y maneja webhooks correctamente.
El RAG y las tools se integran dentro de brain.py sin tocar providers/.

**¿Por qué Gemma 4 y no Claude API en producción?**
Restricción del SERCOP: datos no pueden salir de su infraestructura.
Claude API es cloud. Gemma 4 corre 100% local con Ollama.

**¿Por qué pgvector y no Qdrant/Chroma?**
SERCOP ya opera PostgreSQL en RHEL. Sin nueva infraestructura.
pgvector HNSW coseno + GIN tsvector español = búsqueda híbrida semántica + exacta en un solo motor.

**¿Por qué pipeline RAG propio y no LlamaIndex?**
Control total sobre chunking legal por artículos, RRF, reranker cross-encoder y metadata de artículos.
Más simple, sin dependencias pesadas, fácil de mantener por el equipo TIC.

**¿Por qué tools + RAG y no solo RAG?**
Los montos en USD cambian cada año con el PIE y los plazos de impugnación son datos estructurados precisos.
RAG puede devolver versiones desactualizadas de estos valores. Las tools garantizan exactitud.

**¿Por qué gemma4:e2b y no 26b en el Mac?**
El 26B requiere 17GB en VRAM/RAM. En Mac con 11.7GB VRAM el resto cae a RAM unificada → 3-5 min/respuesta + crash.
El e2b (5B, 6.7GB) responde en ~5 segundos y es suficiente para desarrollo y demos.

---

## 16. Contexto institucional

- **Institución**: SERCOP — Servicio Nacional de Contratación Pública, Ecuador
- **Área**: Coordinación de Tecnología de la Información y Comunicaciones
- **Director TIC**: Paúl Vásquez Méndez
- **Entorno producción**: PostgreSQL en RHEL
- **Restricción crítica**: 100% self-hosted — ningún dato sale de la infraestructura SERCOP
- **Dev environment**: MacBook Pro M5 Space Black, Claude Code, Multipass + 3 VMs PostgreSQL
