---
title: SercoBot
subtitle: Asistente virtual de contratación pública sobre WhatsApp
author: Dirección de Infraestructura y Operaciones (DIO) — SERCOP
date: Mayo 2026
---

# SercoBot
## Asistente virtual de contratación pública sobre WhatsApp

Dirección de Infraestructura y Operaciones (DIO) — SERCOP
Director: Paúl Vásquez Méndez
Transferencia de conocimiento — Mayo 2026

---

## El problema

- Alta demanda ciudadana de información sobre contratación pública.
- Mesa de servicios y call center saturados con preguntas repetitivas.
- Normativa extensa, dispersa y difícil de consultar para un ciudadano común.
- Funcionarios y proveedores requieren respuestas rápidas con base legal.

---

## La solución: SercoBot

Un asistente virtual de WhatsApp que:

- Responde preguntas en lenguaje natural.
- Cita siempre la fuente (artículo, resolución, manual).
- Está disponible 24/7.
- Corre 100% en infraestructura institucional.

---

## ¿En qué se diferencia de ChatGPT?

| Característica | ChatGPT | SercoBot |
|---|---|---|
| Datos en la nube | Sí | **No** — 100% local |
| Cita la fuente | No siempre | **Siempre** |
| Datos exactos (montos/plazos) | Puede fallar | **Garantizados** |
| Costo por uso | Sí | **Gratis** |
| Conoce normativa ecuatoriana | Parcial | **Específico** |

---

## Casos de uso reales

- ¿Cuál es el monto máximo para subasta inversa electrónica en 2026?
- ¿Qué necesito para registrarme como proveedor en el RUP?
- ¿Cuántos días tengo para impugnar una adjudicación?
- ¿Qué proceso uso para contratar consultoría por $50.000?
- ¿Qué es el SICAE?

---

## Estado actual

- **3.059 fragmentos** de normativa indexada
- **17 documentos**: LOSNCP, Reglamento, Resoluciones, Manuales SOCE, Glosario
- Dominio público: `sercobot.sercop.gob.ec`
- Integrado con WhatsApp Business (Meta Cloud API)
- En infraestructura SERCOP (PostgreSQL + servidor de aplicación)

---

## Arquitectura general

```
Usuario WhatsApp
      ↓
Meta Cloud API → Webhook FastAPI
      ↓
brain.py (orquestador)
   ├── Tool calling (datos exactos)
   └── RAG (normativa)
         └── pgvector + reranker
      ↓
Gemma 4 (LLM local) genera respuesta
      ↓
WhatsApp ← respuesta con cita
```

---

## Stack técnico

- **Servidor**: FastAPI + Python 3.11
- **Mensajería**: Meta WhatsApp Cloud API
- **Vector DB**: PostgreSQL 16 + pgvector
- **Embeddings**: nomic-embed-text (Ollama)
- **LLM**: Gemma 4 (Google, open source) vía Ollama
- **Reranker**: cross-encoder mMiniLM

**100% open source · 100% on-premise**

---

## Pipeline RAG

1. Usuario pregunta → expansión de siglas (SIE, RUP, PAC...)
2. Embedding de la consulta
3. Búsqueda híbrida: semántica + léxica en español
4. Fusión RRF (Reciprocal Rank Fusion) → top-12
5. Reranker cross-encoder → top-4
6. Gemma 4 redacta respuesta citando artículo

---

## Tool calling: datos garantizados

5 funciones Python para datos críticos:

- `obtener_montos_pie` — umbrales en USD por proceso
- `recomendar_tipo_contratacion` — proceso según bien/monto
- `obtener_plazos` — días para impugnación, contrato, garantías
- `info_rup` — registro de proveedores
- `obtener_fecha_hora_ecuador` — fecha actual UTC-5

**Regla**: lo que cambia por resolución → tool, no RAG.

---

## Anti-alucinación

Tres barreras de seguridad:

1. **System prompt** prohíbe inventar artículos.
2. **Datos volátiles** (montos, plazos) en tablas controladas, no en el LLM.
3. **Si no encuentra**: redirige al portal oficial en vez de inventar.

Catastrófico en sector público dar un número de artículo falso.

---

## Soberanía del dato

- **Ningún dato sale de la infraestructura SERCOP.**
- LLM corre local (Ollama).
- Embeddings locales.
- Base de datos institucional.
- Cumple con restricciones de manejo de información pública.

---

## Recursos para replicarlo

| Recurso | Detalle |
|---|---|
| Equipo | 1 dev Python + 1 experto del dominio |
| Hardware | Servidor Linux + GPU (16 GB VRAM) |
| BD | PostgreSQL 16 |
| WhatsApp | Cuenta Meta Business |
| Documentos | PDFs propios de la entidad |
| Tiempo MVP | 6-8 semanas |

---

## Costos

- Infraestructura: hardware existente
- Software: 100% open source
- Modelo IA: Gemma 4 — gratis
- WhatsApp: gratis hasta cierto volumen
- **Costo recurrente: solo electricidad del servidor**

---

## Roadmap de réplica

| Fase | Duración |
|---|---|
| Análisis del dominio | 1 sem |
| Infraestructura | 1 sem |
| Ingesta de documentos | 2 sem |
| Adaptación del agente | 2 sem |
| Integración WhatsApp | 1 sem |
| Pruebas y calibración | 1 sem |
| **Total** | **6-8 sem** |

---

## Lo crítico para no fracasar

1. Chunking por artículo legal, no por tamaño.
2. Búsqueda híbrida obligatoria (semántica + léxica).
3. Datos volátiles fuera del LLM.
4. Anti-alucinación explícita en el prompt.
5. Modelo según hardware disponible.
6. Capa WhatsApp aislada (multi-provider).
7. Deduplicación de mensajes (Meta reintenta).
8. Soberanía del dato como requisito no negociable.

---

## Lo que se reusa vs. lo que se rehace

**Se reusa al 100%**
- Capa WhatsApp (providers)
- Arquitectura RAG
- Pipeline de ingesta
- Tool calling framework

**Se rehace por dominio**
- System prompt
- Las 5 tools (datos propios)
- Base de conocimiento (normativa de la entidad)
- Siglas y vocabulario

---

## Preguntas frecuentes

- **¿Costo mensual?** Solo electricidad.
- **¿Si se cae?** Mensajes encolados en Meta, sin pérdida.
- **¿Puede equivocarse?** Mínimo, con evaluación RAGAS periódica.
- **¿Datos personales?** Solo número y conversación, en BD institucional.
- **¿Quién mantiene la normativa?** Área dueña del dominio, no la DIO.

---

## Próximos pasos

Para la entidad interesada:

1. Definir alcance y dominio normativo.
2. Levantar infraestructura mínima.
3. Sesión técnica con DIO SERCOP.
4. Acceso al repositorio bajo convenio.
5. Acompañamiento durante MVP.

---

# Gracias

**SercoBot — Dirección de Infraestructura y Operaciones (DIO) — SERCOP**
Director: Paúl Vásquez Méndez
sercobot.sercop.gob.ec
