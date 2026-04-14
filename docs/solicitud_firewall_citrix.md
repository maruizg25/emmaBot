# Solicitud de Apertura de Reglas de Firewall / Citrix
## Sistema SARA — Asistente Virtual de Contratación Pública
**SERCOP — Coordinación de Tecnología de la Información y Comunicaciones**
Fecha: Abril 2026

---

## 1. Descripción del Sistema

SARA es un asistente virtual de WhatsApp que responde preguntas sobre normativa
de contratación pública. El sistema corre en el servidor Linux de la Coordinación TIC
y requiere conectividad saliente hacia los siguientes servicios externos.

**Servidor de origen:** `app-bdd-chatbot` (infraestructura interna SERCOP)
**Dirección de tráfico:** Todo es **SALIENTE** desde el servidor hacia Internet.
No se requieren puertos de entrada gracias al uso de Cloudflare Tunnel.

---

## 2. Reglas de Firewall Requeridas

### 2.1 OBLIGATORIAS — Sistema no funciona sin estas

| # | Servicio | Host / Dominio | Puerto | Protocolo | Justificación |
|---|---|---|---|---|---|
| 1 | **Groq API (LLM)** | `api.groq.com` | 443 | HTTPS/TCP | Generación de respuestas del asistente virtual |
| 2 | **Meta WhatsApp API** | `graph.facebook.com` | 443 | HTTPS/TCP | Envío de respuestas a usuarios de WhatsApp |
| 3 | **Cloudflare Tunnel** | `*.cloudflareaccess.com` | 443 | HTTPS/TCP | Recepción de mensajes de WhatsApp (webhook) |
| 4 | **Cloudflare Tunnel** | `*.cloudflare.com` | 443 | HTTPS/TCP | Infraestructura del túnel seguro |
| 5 | **Cloudflare Tunnel** | `*.trycloudflare.com` | 7844 | TCP/UDP | Canal de datos del túnel (alternativa: 443) |
| 6 | **Cloudflare IPs** | `198.41.192.0/20` | 7844 | TCP/UDP | IPs Anycast de Cloudflare para el túnel |
| 7 | **Cloudflare IPs** | `198.41.200.0/20` | 7844 | TCP/UDP | IPs Anycast de Cloudflare para el túnel |

> **Nota sobre Cloudflare Tunnel:** El servicio `cloudflared` establece conexiones
> **salientes** desde el servidor hacia Cloudflare. Cloudflare reenvía los webhooks
> de Meta a través de ese túnel. No se abren puertos de entrada en el servidor.

---

### 2.2 MANTENIMIENTO — Necesarias para actualizaciones del sistema

| # | Servicio | Host / Dominio | Puerto | Protocolo | Justificación |
|---|---|---|---|---|---|
| 8 | **GitHub** | `github.com` | 443 | HTTPS/TCP | Descarga de actualizaciones del código fuente |
| 9 | **GitHub** | `objects.githubusercontent.com` | 443 | HTTPS/TCP | Descarga de archivos de GitHub |
| 10 | **PyPI (Python)** | `pypi.org` | 443 | HTTPS/TCP | Instalación de dependencias Python (`pip install`) |
| 11 | **PyPI (archivos)** | `files.pythonhosted.org` | 443 | HTTPS/TCP | Descarga de paquetes Python |

---

### 2.3 MODELOS IA — Necesarias para descarga/actualización de modelos (uso esporádico)

| # | Servicio | Host / Dominio | Puerto | Protocolo | Justificación |
|---|---|---|---|---|---|
| 12 | **Ollama Registry** | `ollama.com` | 443 | HTTPS/TCP | Descarga de modelos de lenguaje (LLM local) |
| 13 | **Ollama Registry** | `registry.ollama.ai` | 443 | HTTPS/TCP | Descarga de modelos de lenguaje (LLM local) |
| 14 | **HuggingFace** | `huggingface.co` | 443 | HTTPS/TCP | Descarga del modelo de reranking (una sola vez) |
| 15 | **HuggingFace CDN** | `cdn-lfs.huggingface.co` | 443 | HTTPS/TCP | Archivos de modelos HuggingFace |
| 16 | **HuggingFace CDN** | `cdn-lfs-us-1.huggingface.co` | 443 | HTTPS/TCP | Archivos de modelos HuggingFace (mirror) |

> **Nota:** Los modelos #12-16 se descargan una sola vez y quedan almacenados
> localmente en el servidor. Después de la instalación inicial estas reglas
> pueden ser temporales o eliminadas.

---

### 2.4 OPCIONALES — Para monitoreo y diagnóstico

| # | Servicio | Host / Dominio | Puerto | Protocolo | Justificación |
|---|---|---|---|---|---|
| 17 | **Groq Console** | `console.groq.com` | 443 | HTTPS/TCP | Monitoreo de uso y límites de la API |
| 18 | **WhatsApp Business** | `business.facebook.com` | 443 | HTTPS/TCP | Gestión del número de WhatsApp Business |

---

## 3. Resumen de Puertos por Protocolo

| Puerto | Protocolo | Servicios |
|---|---|---|
| **443** | HTTPS/TCP | Groq, Meta, Cloudflare, GitHub, PyPI, Ollama, HuggingFace |
| **7844** | TCP + UDP | Cloudflare Tunnel (canal de datos) |

> Si el puerto 7844 está bloqueado, `cloudflared` usa automáticamente el puerto 443
> como fallback. Se recomienda abrir 7844 para mejor rendimiento del túnel.

---

## 4. Diagrama de Flujo de Tráfico

```
[Servidor SERCOP app-bdd-chatbot]
          |
          | SALIENTE :443 (HTTPS)
          ↓
   [Cloudflare Tunnel] ←→ [Meta WhatsApp] (webhook entrante → túnel)
          |
          | SALIENTE :443 (HTTPS)
          ↓
     [Groq API] — genera respuesta con Llama 4 Scout 17B
          |
          | SALIENTE :443 (HTTPS)
          ↓
  [Meta graph.facebook.com] — envía respuesta al usuario WhatsApp
          |
          ↓
   [Usuario WhatsApp]
```

**No hay puertos de ENTRADA abiertos en el servidor.**
Todo el tráfico es iniciado desde el servidor hacia el exterior.

---

## 5. Verificación de Conectividad

Para verificar que las reglas están aplicadas correctamente, ejecutar
en el servidor después de los cambios:

```bash
cd /home/jonathan.ruiz/sara-sercop
python scripts/detectar_firewall.py --salida reporte_post_firewall.txt
```

El script prueba cada endpoint y genera un reporte de resultados.

---

## 6. Consideraciones de Seguridad

| Punto | Detalle |
|---|---|
| **Tráfico saliente únicamente** | No se abren puertos de entrada en el servidor |
| **Todo en HTTPS/TLS** | Todas las conexiones van cifradas |
| **Datos a Groq** | Los textos de consultas y normativa viajan a Groq (EE.UU.). Validar con área legal. |
| **Datos a Meta** | El texto de las respuestas viaja a Meta (WhatsApp). Requerimiento del servicio. |
| **Datos locales** | Base de datos PostgreSQL, embeddings y modelos Ollama permanecen en el servidor. |

---

## 7. Contacto Técnico

**Responsable:** Coordinación de TIC — SERCOP Ecuador
**Sistema:** SARA v2.0 — Asistente Virtual de Contratación Pública
**Servidor:** `app-bdd-chatbot` (Linux RHEL)
