# Transferencia de Conocimiento — SercoBot

**Asistente virtual de WhatsApp para consultas de contratación pública**
SERCOP — Dirección de Infraestructura y Operaciones (DIO)
Mayo 2026

---

## 1. Introducción

SercoBot es un asistente virtual de WhatsApp que responde preguntas sobre normativa de contratación pública ecuatoriana. Está dirigido a **ciudadanos y proveedores del Estado**, no solo a funcionarios. Cita siempre la fuente legal (artículo, resolución, manual).

El presente documento sirve como base para la **transferencia de conocimiento** a otra entidad gubernamental que desee replicar el desarrollo o asumir la continuidad técnica del proyecto.

---

## 2. Notas no técnicas

### 2.1 ¿Qué problema resuelve?

- Reduce la carga de atención al ciudadano en mesa de servicios y call center.
- Democratiza el acceso a la normativa: cualquier persona puede preguntar "¿cuánto puedo facturar como proveedor?" o "¿qué proceso necesito para comprar X?" y recibir la respuesta con la base legal.
- Disponible 24/7 por un canal que la gente ya usa (WhatsApp).
- Respuestas trazables: cada respuesta cita el artículo o resolución, no inventa.

### 2.2 ¿Qué lo hace diferente de un ChatGPT?

1. **Soberanía del dato**: corre 100% dentro de la infraestructura institucional. Ningún dato del ciudadano ni de la normativa sale a la nube. Crítico en sector público.
2. **No alucina**: responde solo con base en documentos oficiales cargados (Ley, Reglamento, Resoluciones, Manuales). Si no encuentra la respuesta, lo dice.
3. **Datos precisos garantizados**: montos en USD y plazos legales no se sacan del modelo de IA — se sacan de tablas estructuradas que el equipo DIO controla y actualiza.
4. **Cita la fuente**: cada respuesta indica el artículo o resolución, igual que un abogado.

### 2.3 Casos de uso reales

- "¿Cuál es el monto máximo para subasta inversa electrónica en 2026?"
- "¿Qué necesito para registrarme como proveedor en el RUP?"
- "¿Cuántos días tengo para impugnar una adjudicación?"
- "¿Qué proceso uso para contratar consultoría por $50.000?"
- "¿Qué es el SICAE?"

### 2.4 Estado actual

- **3.059 fragmentos** de normativa indexada (17 documentos: LOSNCP, Reglamento, Resoluciones, Manuales SOCE, Glosario, Código de Ética).
- Publicado en dominio institucional `sercobot.sercop.gob.ec`.
- Integrado con WhatsApp Business (Meta Cloud API).
- Corre en infraestructura SERCOP (PostgreSQL + servidor de aplicación).
- Pendiente: evaluación formal de calidad (RAGAS) y deploy del modelo grande en servidor con GPU.

### 2.5 Recursos para replicarlo en otra entidad

| Recurso | Detalle |
|---|---|
| Equipo humano | 1 desarrollador full-stack Python + 1 experto del dominio normativo |
| Infraestructura | 1 servidor Linux (RHEL/Ubuntu) con GPU recomendada (16 GB VRAM mínimo) + PostgreSQL 16 |
| Cuenta WhatsApp | Meta Cloud API (gratis hasta cierto volumen) o Twilio/Whapi |
| Documentos fuente | PDFs oficiales de la normativa propia de la entidad |
| Tiempo MVP | 6-8 semanas con base existente |

### 2.6 Costos aproximados

- **Infraestructura**: usa hardware existente. Sin licencias nuevas.
- **Software**: 100% open source (PostgreSQL, Ollama, Python, FastAPI).
- **Modelo de IA**: Gemma 4 de Google — gratis, sin pago por uso ni por token.
- **WhatsApp**: Meta Cloud API gratis para conversaciones iniciadas por el usuario hasta cierto volumen mensual.

---

## 3. Notas técnicas

### 3.1 Arquitectura general

```
Usuario WhatsApp
      ↓
Meta Cloud API → POST /webhook
      ↓
FastAPI (Python) — capa de providers
      ↓
brain.py (orquestador)
   ├── Tool calling: ¿pregunta de monto/plazo/RUP/fecha?
   │     └─→ función Python con datos exactos y estructurados
   └── RAG: pregunta normativa general
         ├── expansión de siglas (SIE, RUP, PAC...)
         ├── embedding (nomic-embed-text 768d)
         ├── búsqueda híbrida (semántica + texto exacto)
         ├── fusión RRF (Reciprocal Rank Fusion)
         ├── reranker (cross-encoder mMiniLM)
         └── top-4 chunks con metadata de artículo
      ↓
Gemma 4 (LLM local vía Ollama) genera respuesta citando fuente
      ↓
WhatsApp ← respuesta
```

### 3.2 Stack técnico

| Capa | Tecnología | Justificación |
|---|---|---|
| Servidor | FastAPI + Uvicorn (Python 3.11+) | Async nativo, ligero, mantenible |
| Mensajería | Meta WhatsApp Cloud API | Oficial, gratis, multi-provider abstraído |
| Memoria conversacional | SQLAlchemy + SQLite (dev) / PostgreSQL (prod) | Historial por número telefónico |
| Vector DB | PostgreSQL 16 + pgvector 0.8.2 | Sin nueva infra; ya operada |
| Búsqueda híbrida | HNSW coseno + GIN tsvector español | Semántica + exactitud léxica |
| Embeddings | nomic-embed-text 768d (Ollama) | Open source, multilingüe |
| LLM | Gemma 4 (e2b dev / 26b prod) vía Ollama | 100% local, soberano |
| Reranker | cross-encoder mMiniLMv2 | Sube precisión del top-4 |

### 3.3 Pipeline RAG en detalle

**Ingesta** (`scripts/scraper_biblioteca.py`, `ingestar_knowledge.py`)
Lee PDFs → chunking jerárquico por artículo legal → genera embeddings → guarda en `chunks_sercop`. 91.5% de chunks tienen metadata `{articulo: "Art. 74"}` para citación precisa.

**Recuperación** (`agent/retriever.py`)
Expande siglas SERCOP en la query → búsqueda vectorial (HNSW) + búsqueda léxica (tsvector) → fusiona resultados con RRF (top-12) → cross-encoder rerankea (top-4 final).

**Generación** (`agent/brain.py`)
System prompt v3 con anti-alucinación → decide entre tool calling (datos exactos) o RAG (normativa general) → Gemma 4 redacta respuesta basada en chunks recuperados.

### 3.4 Tool calling (5 tools)

Separamos los datos que **deben ser exactos** (montos, plazos) del razonamiento normativo. Las tools son funciones Python con datos controlados por el equipo DIO:

| Tool | Para qué |
|---|---|
| `obtener_montos_pie` | Umbrales en USD por tipo de proceso (PIE 2025/2026) |
| `recomendar_tipo_contratacion` | Sugiere proceso dado bien/servicio/monto |
| `obtener_plazos` | Días para impugnación, contrato, garantías |
| `info_rup` | Requisitos, costo y suspensión del RUP |
| `obtener_fecha_hora_ecuador` | Fecha/hora actual UTC-5 |

**Regla de oro**: cualquier dato que cambie por resolución periódica (montos PIE) NO va al RAG, va a una tool. El RAG es solo para texto normativo.

### 3.5 Estructura del repositorio

```
emmaBot/
├── agent/
│   ├── main.py            ← Webhook FastAPI
│   ├── brain.py           ← Orquestador LLM + RAG + tools
│   ├── retriever.py       ← Pipeline RAG híbrido
│   ├── tools.py           ← 5 funciones de datos exactos
│   ├── memory.py          ← Historial conversacional
│   └── providers/         ← Whapi / Meta / Twilio (intercambiables)
├── config/
│   ├── prompts.yaml       ← System prompt v3
│   └── business.yaml      ← Datos institucionales
├── knowledge/             ← PDFs fuente
├── scripts/               ← Ingesta y scraping
└── tests/                 ← Chat local sin WhatsApp
```

### 3.6 Variables de entorno críticas

```env
WHATSAPP_PROVIDER=meta
META_ACCESS_TOKEN=...
RAG_ENABLED=true
LLM_MODE=gemma4
OLLAMA_MODEL=gemma4:e2b
POSTGRES_HOST=192.168.2.2
POSTGRES_DB=sercop_db
```

### 3.7 Infraestructura productiva

- **DNS**: `sercobot.sercop.gob.ec` → IP pública 157.100.62.125
- **NAT**: 157.100.62.125 → 192.168.100.131 (DMZ)
- **Citrix**: reverse proxy → 192.168.9.230:8000 (servidor app)
- **PostgreSQL + pgvector**: VM en 192.168.2.2

---

## 4. Puntos críticos para transferencia

Si otra persona o entidad va a replicar el sistema, estos son los puntos donde la mayoría se equivoca:

### 4.1 Calidad del RAG = calidad del chunking
No basta con cargar PDFs y trocearlos por tamaño. El chunking debe respetar **artículos legales** y guardar el número de artículo en metadata. Sin esto, el bot no podrá citar la fuente y el ciudadano no puede verificar.

### 4.2 Búsqueda híbrida no es opcional
- Solo búsqueda semántica → falla en consultas con números ("Art. 74", "RE-SERCOP-2024-0144").
- Solo búsqueda léxica → falla en sinónimos ("proveedor" vs "contratista").
- La combinación con RRF es lo que hace que funcione.

### 4.3 Datos volátiles fuera del LLM
Los montos PIE cambian cada año. Si los dejas en el RAG, el bot citará valores viejos. **Regla**: cualquier dato que dependa de una resolución que cambia → tool, no RAG.

### 4.4 Anti-alucinación explícita en el prompt
El prompt v3 tiene una regla: "Si el chunk no contiene el artículo explícito → no inventes el número, di que verifiquen en el portal oficial." Sin esta regla, los LLMs inventan números de artículo plausibles. Catastrófico en sector público.

### 4.5 Selección del modelo según hardware
- Gemma 4 26B → requiere 18 GB de VRAM. En GPU dedicada va bien.
- Sin GPU → usar Gemma 4 e2b (5 GB, ~5s/respuesta).
- 26B en hardware insuficiente = crash + 3-5 min/respuesta. Probado.

### 4.6 Capa de WhatsApp aislada
`providers/` abstrae Meta / Whapi / Twilio detrás de una interfaz común. Si cambian de proveedor, solo se toca esa carpeta. No mezclar lógica de WhatsApp con lógica del agente.

### 4.7 Deduplicación de mensajes
Meta reintenta webhooks. Sin un `set` de mensajes procesados, el bot responde 2-3 veces el mismo mensaje.

### 4.8 Soberanía del dato como requisito no negociable
En sector público es lo que justifica el proyecto frente a usar ChatGPT/Claude API. Toda la pila debe poder correr offline (Ollama, pgvector, sentence-transformers descargados localmente).

---

## 5. Roadmap sugerido para una entidad replicante

| Fase | Duración | Entregable |
|---|---|---|
| 1. Análisis del dominio | 1 semana | Inventario de normativa propia + casos de uso |
| 2. Infraestructura | 1 semana | PostgreSQL + pgvector + Ollama + servidor |
| 3. Ingesta de documentos | 2 semanas | Chunking por artículo + indexación + validación |
| 4. Adaptación del agente | 2 semanas | System prompt + tools propias + siglas del dominio |
| 5. Integración WhatsApp | 1 semana | Cuenta Meta Business + webhook + dominio público |
| 6. Pruebas y calibración | 1 semana | RAGAS + pruebas de escritorio + ajuste prompt |

| **Total** | **6-8 semanas** | MVP en producción |

---

## 6. Preguntas frecuentes (para la reunión)

**¿Cuánto cuesta operarlo al mes?**
Solo el costo de electricidad del servidor. No hay licencias, no hay pago por uso de IA, no hay pago por mensaje de WhatsApp (hasta el límite gratis de Meta).

**¿Qué pasa si se cae Ollama o el servidor?**
El bot deja de responder y los mensajes quedan encolados en Meta. Cuando vuelve, los procesa. No hay pérdida de datos.

**¿Puede confundirse y dar información incorrecta?**
El diseño minimiza alucinaciones: solo cita lo que está en los documentos oficiales y los datos críticos (montos, plazos) salen de tablas controladas. Aun así, se recomienda evaluación periódica con RAGAS y revisión humana de muestras.

**¿Es accesible para personas con discapacidad?**
WhatsApp ya cumple estándares de accesibilidad nativos. El bot responde en texto plano, compatible con lectores de pantalla.

**¿Se puede usar el mismo código tal cual?**
La capa de WhatsApp y la arquitectura RAG se reusan al 100%. El system prompt, las tools y la base de conocimiento son específicos del SERCOP — eso lo tiene que rehacer la entidad para su dominio.

**¿Quién mantiene la normativa actualizada?**
Es responsabilidad del área de la entidad dueña del dominio (no de la DIO). Cuando se publica una nueva resolución, se ejecuta el script de ingesta y queda disponible.

**¿Qué pasa con datos personales del ciudadano?**
El bot solo guarda el número de WhatsApp y el historial de la conversación, en la base de datos institucional. Nada sale a internet ni a terceros.

---

## 7. Contacto técnico

- **Institución**: SERCOP — Servicio Nacional de Contratación Pública del Ecuador
- **Área**: Dirección de Infraestructura y Operaciones (DIO)
- **Director DIO**: Paúl Vásquez Méndez
- **Repositorio**: disponible bajo solicitud a la DIO
