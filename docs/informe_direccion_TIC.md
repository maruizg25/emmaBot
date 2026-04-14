# Informe Técnico y Solicitud de Infraestructura
## Sistema SARA — Asistente Virtual de Contratación Pública

**Para:** Ing. Paúl Vásquez Méndez
**Cargo:** Director de Infraestructura y Operaciones
**Unidad:** Coordinación de Tecnología de la Información y Comunicaciones — SERCOP
**Fecha:** Abril 2026
**Asunto:** Estado del sistema SARA y requerimientos de infraestructura para puesta en producción

---

## 1. Resumen Ejecutivo

El sistema **SARA** (Sistema de Asesoría y Respuesta Automatizada) ha sido
desarrollado e instalado exitosamente en el servidor de producción del SERCOP.
SARA es un asistente virtual de WhatsApp que responde preguntas ciudadanas sobre
normativa de contratación pública, citando artículos de la LOSNCP, el Reglamento
General y resoluciones vigentes.

Las pruebas técnicas han sido completadas con resultados satisfactorios:
tiempos de respuesta de **1 a 2 segundos** y respuestas precisas con citación
normativa. El sistema está listo para conectarse a WhatsApp Business y entrar
en operación, pendiente únicamente de la habilitación de infraestructura de red
descrita en este documento.

---

## 2. Estado Actual del Sistema

### 2.1 Componentes instalados y funcionando

| Componente | Estado | Ubicación |
|---|---|---|
| Servidor de la aplicación (FastAPI) | ✅ Operativo | `app-bdd-chatbot` puerto 8000 |
| Motor de IA local (Ollama + qwen2.5:3b) | ✅ Operativo | `app-bdd-chatbot` puerto 11434 |
| Base de datos de conversaciones (PostgreSQL) | ✅ Operativo | `app-bdd-chatbot` puerto 5432 |
| Base de conocimiento normativa (RAG) | ✅ Operativo | PostgreSQL local — 3,059 fragmentos |
| Modelo de embeddings (nomic-embed-text) | ✅ Operativo | Ollama local |
| API de IA externa (Groq — Llama 4 Scout 17B) | ✅ Configurado | api.groq.com |
| Túnel Cloudflare (cloudflared) | ✅ Instalado | `app-bdd-chatbot` |
| Conexión a WhatsApp Business (Meta API) | ⏳ Pendiente configuración webhook | Requiere URL pública estable |

### 2.2 Base de Conocimiento Normativa

El sistema tiene indexados **3,059 fragmentos** de 17 documentos oficiales:

| Documento | Tipo |
|---|---|
| Reglamento General LOSNCP — octubre 2025 | Reglamento |
| Normativa Secundaria de Contratación Pública 2025 | Reglamento |
| LOSNCP — Registro Oficial 140, 07-X-2025 | Ley |
| Manual SOCE — Subasta Inversa Electrónica | Manual |
| RE-SERCOP-2026-0001, 0002 y otras resoluciones | Resoluciones |
| Modelo de Pliego SICAE, Código de Ética, Instructivos | Varios |

### 2.3 Capacidades del Asistente

| Capacidad | Descripción |
|---|---|
| Consultas normativas | Responde con citación de artículo (LOSNCP, RGLOSNCP, Resoluciones) |
| Montos de contratación | Umbrales exactos 2025/2026 por tipo de proceso (ínfima cuantía, menor cuantía, licitación, etc.) |
| Plazos | Días hábiles por proceso, impugnación, contratos y garantías |
| Registro de Proveedores | Requisitos, costos y proceso del RUP |
| Tipo de proceso | Orientación sobre qué modalidad usar según monto y objeto |
| Fecha y hora Ecuador | Información en tiempo real |

### 2.4 Rendimiento Comprobado

| Métrica | Resultado |
|---|---|
| Tiempo de respuesta promedio | **1.1 — 1.9 segundos** |
| Tiempo de respuesta máximo | < 3 segundos |
| Usuarios simultáneos soportados (plan gratuito) | Hasta 30 por minuto |
| Conversaciones diarias (plan gratuito) | ~200 por día |
| Disponibilidad del servidor local | 24/7 (proceso del sistema operativo) |

---

## 3. Arquitectura de la Solución

```
 CIUDADANO / PROVEEDOR DEL ESTADO
         │ WhatsApp
         ▼
 ┌───────────────────┐
 │  Meta WhatsApp    │  ← Canal de comunicación
 │  Business API     │
 └────────┬──────────┘
          │ HTTPS (webhook)
          ▼
 ┌───────────────────┐
 │  Cloudflare CDN   │  ← Termina el TLS / HTTPS
 │  sara.sercop.gob.ec│    Certificado automático
 └────────┬──────────┘
          │ Túnel cifrado (saliente desde servidor)
          ▼
 ┌─────────────────────────────────────────┐
 │     SERVIDOR app-bdd-chatbot — SERCOP   │
 │                                         │
 │  SARA (FastAPI)      → procesa consulta │
 │  Base normativa RAG  → busca normativa  │
 │  Ollama (IA local)   → embeddings       │
 │  PostgreSQL          → historial        │
 └──────────────┬──────────────────────────┘
                │ HTTPS saliente
                ▼
 ┌───────────────────┐
 │   Groq API        │  ← Genera la respuesta
 │ (Llama 4 Scout)   │    en lenguaje natural
 └───────────────────┘
```

**Principio de seguridad:** El servidor no recibe conexiones externas directas.
Todo el tráfico entrante pasa por Cloudflare Tunnel, que el servidor establece
como conexión saliente. No se requiere abrir puertos de entrada en el firewall.

---

## 4. Requerimientos de Infraestructura para Producción

### 4.1 Solicitud al Equipo de DNS

Crear el siguiente registro en el servidor DNS institucional del SERCOP:

| Campo | Valor |
|---|---|
| **Tipo de registro** | CNAME |
| **Nombre** | `sara` |
| **Dominio resultante** | `sara.sercop.gob.ec` |
| **Destino (apunta a)** | `<ID_TUNEL>.cfargotunnel.com` *(TIC entrega el ID)* |
| **TTL** | 300 segundos |

> El ID del túnel será proporcionado por la Coordinación TIC una vez confirmada
> esta solicitud. El proceso toma menos de 5 minutos una vez que TIC ejecuta
> el comando de creación del túnel.

**Certificado HTTPS:** Cloudflare emite y renueva automáticamente el certificado
SSL/TLS para `sara.sercop.gob.ec`. El equipo de redes no requiere gestionar
ningún certificado.

---

### 4.2 Solicitud al Equipo de Firewall / Citrix

#### REGLAS OBLIGATORIAS — Sin estas el sistema no opera

| # | Destino | Puerto | Protocolo | Para qué sirve |
|---|---|---|---|---|
| 1 | `api.groq.com` | 443 | HTTPS | Motor de IA — genera las respuestas |
| 2 | `graph.facebook.com` | 443 | HTTPS | Envío de respuestas a WhatsApp |
| 3 | `*.cloudflare.com` | 443 | HTTPS | Túnel — recepción de mensajes WhatsApp |
| 4 | `*.cfargotunnel.com` | 443 | HTTPS | URL del túnel en producción |
| 5 | `cloudflareaccess.com` | 443 | HTTPS | Autenticación del túnel |
| 6 | `region1.v2.argotunnel.com` | 7844 | TCP+UDP | Canal de datos del túnel (rendimiento) |
| 7 | `region2.v2.argotunnel.com` | 7844 | TCP+UDP | Canal de datos del túnel (redundancia) |

#### REGLAS DE MANTENIMIENTO — Para actualizaciones del sistema

| # | Destino | Puerto | Protocolo | Para qué sirve |
|---|---|---|---|---|
| 8 | `github.com` | 443 | HTTPS | Actualizaciones del código fuente |
| 9 | `objects.githubusercontent.com` | 443 | HTTPS | Archivos de GitHub |
| 10 | `pypi.org` | 443 | HTTPS | Dependencias Python |
| 11 | `files.pythonhosted.org` | 443 | HTTPS | Paquetes Python |

#### REGLAS DE MODELOS IA — Solo instalación inicial (pueden ser temporales)

| # | Destino | Puerto | Protocolo | Para qué sirve |
|---|---|---|---|---|
| 12 | `ollama.com` | 443 | HTTPS | Descarga de modelos de IA locales |
| 13 | `registry.ollama.ai` | 443 | HTTPS | Repositorio de modelos Ollama |
| 14 | `huggingface.co` | 443 | HTTPS | Modelo de búsqueda semántica |
| 15 | `cdn-lfs.huggingface.co` | 443 | HTTPS | Archivos de modelos IA |

> Una vez instalados los modelos de IA (#12-15), estas reglas pueden eliminarse.

---

## 5. Consideraciones de Privacidad y Seguridad

| Aspecto | Detalle |
|---|---|
| **Datos que permanecen en SERCOP** | Base de datos normativa (3,059 fragmentos), historial de conversaciones, modelos de IA locales, código fuente |
| **Datos que salen al exterior** | Texto de las consultas ciudadanas y fragmentos normativos relevantes viajan a Groq (EE.UU.) para generar la respuesta. Se recomienda revisión del área legal bajo la normativa de protección de datos del Ecuador. |
| **Cifrado** | Todo el tráfico usa TLS 1.3 (HTTPS). El túnel Cloudflare agrega una capa adicional de cifrado. |
| **Puertos de entrada** | Ninguno. El servidor no acepta conexiones externas directas. |
| **Alternativa 100% local** | Si la restricción de datos impide el uso de Groq, el sistema puede operar con el modelo de IA local (Ollama) a un costo de ~50 segundos de respuesta en lugar de 1-2 segundos, sin requerir ninguna conexión externa para la generación de respuestas. |

---

## 6. Costos Proyectados

| Concepto | Costo | Frecuencia |
|---|---|---|
| Groq API — plan gratuito (hasta ~200 conv/día) | **$0** | Mensual |
| Groq API — plan de pago (si supera el gratuito) | **$3 — $56** | Mensual según uso |
| Cloudflare Tunnel | **$0** | Gratuito |
| Dominio `sara.sercop.gob.ec` | **$0** | Usa dominio institucional existente |
| Infraestructura del servidor | Ya existente | — |

**Costo operativo estimado para el primer año: $0 — $56/mes** según volumen de uso,
con infraestructura de servidor ya amortizada.

---

## 7. Pasos para Puesta en Producción

Una vez aprobada y ejecutada la habilitación de infraestructura:

| Paso | Responsable | Acción |
|---|---|---|
| 1 | **Equipo Firewall** | Aplicar reglas de firewall (sección 4.2) |
| 2 | **Coordinación TIC** | Crear túnel: `cloudflared tunnel create sara` y entregar ID al equipo DNS |
| 3 | **Equipo DNS** | Crear registro CNAME `sara.sercop.gob.ec` con el ID entregado |
| 4 | **Coordinación TIC** | Verificar HTTPS: `curl https://sara.sercop.gob.ec/` |
| 5 | **Coordinación TIC** | Registrar webhook en Meta: `https://sara.sercop.gob.ec/webhook` |
| 6 | **Coordinación TIC** | Activar SARA como servicio del sistema (systemd) |
| 7 | **Coordinación TIC** | Prueba funcional con número WhatsApp Business |

**Tiempo estimado de implementación:** 1 día hábil una vez habilitada la infraestructura.

---

## 8. Verificación Técnica

Para confirmar que las reglas de firewall están correctamente aplicadas,
ejecutar el script de diagnóstico incluido en el sistema:

```bash
cd /home/jonathan.ruiz/sara-sercop
python scripts/detectar_firewall.py --salida reporte_verificacion.txt
```

Genera un reporte con estado ✅/❌ por cada endpoint requerido.

---

*Documento generado por la Coordinación de TIC — SERCOP Ecuador*
*Sistema SARA v2.0 — Abril 2026*
