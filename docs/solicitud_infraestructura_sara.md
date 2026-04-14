# Solicitud de Infraestructura de Red — Sistema SARA
## Asistente Virtual de Contratación Pública — SERCOP
**Dirigido a:** Equipo de Redes, DNS y Seguridad Informática
**Solicitante:** Coordinación de Tecnología de la Información y Comunicaciones
**Fecha:** Abril 2026
**Prioridad:** Alta — requerido para puesta en producción

---

## 1. Contexto

La Coordinación TIC del SERCOP ha desarrollado **SARA** (Sistema de Asesoría y
Respuesta Automatizada), un asistente virtual de WhatsApp que responde preguntas
sobre normativa de contratación pública. El sistema ya está instalado y probado
en el servidor `app-bdd-chatbot`.

Para entrar en producción se requieren tres acciones del equipo de infraestructura:
1. Creación de un subdominio DNS
2. Apertura de reglas de firewall / Citrix
3. El certificado HTTPS es gestionado automáticamente por Cloudflare (no requiere acción del equipo)

---

## 2. Solicitud 1 — Registro DNS (CNAME)

### Qué se necesita

Crear un registro **CNAME** en el servidor DNS del SERCOP:

| Campo | Valor |
|---|---|
| **Tipo** | CNAME |
| **Nombre (subdominio)** | `sara` |
| **Dominio completo resultante** | `sara.sercop.gob.ec` |
| **Apunta a (destino)** | `<ID_TUNEL>.cfargotunnel.com` |
| **TTL** | 300 segundos (5 minutos) |
| **Proxy Cloudflare** | Sí (nube naranja en Cloudflare) |

> **Nota:** El valor `<ID_TUNEL>` será proporcionado por la Coordinación TIC
> una vez que se cree el túnel en Cloudflare. El proceso es:
> 1. TIC ejecuta `cloudflared tunnel create sara` en el servidor
> 2. Cloudflare genera un ID único (ej: `a1b2c3d4-xxxx-xxxx-xxxx-xxxxxxxxxxxx`)
> 3. TIC entrega ese ID al equipo de DNS para completar el CNAME

### Por qué este subdominio

`sara.sercop.gob.ec` es la URL pública que se configurará como webhook en
Meta WhatsApp Business. Esta URL debe ser estable y con HTTPS válido para
que Meta la acepte.

### Certificado HTTPS

**No se requiere gestión manual del certificado.** Cloudflare emite y renueva
automáticamente un certificado SSL/TLS gratuito (Cloudflare CA o Let's Encrypt)
para `sara.sercop.gob.ec` una vez que el CNAME esté activo y apuntando al túnel.
El equipo de redes no necesita instalar ni renovar ningún certificado.

---

## 3. Solicitud 2 — Reglas de Firewall / Citrix

Todo el tráfico es **SALIENTE** desde el servidor `app-bdd-chatbot` hacia Internet.
**No se requiere abrir puertos de entrada** — el sistema usa Cloudflare Tunnel,
que establece conexiones salientes desde el servidor.

### 3.1 Reglas OBLIGATORIAS (sistema no funciona sin estas)

| # | Servicio | Host de destino | Puerto | Protocolo | Justificación |
|---|---|---|---|---|---|
| 1 | **Groq API** | `api.groq.com` | 443 | HTTPS/TCP | Motor de IA que genera las respuestas del asistente |
| 2 | **Meta WhatsApp** | `graph.facebook.com` | 443 | HTTPS/TCP | Envío de respuestas a usuarios de WhatsApp |
| 3 | **Cloudflare Tunnel** | `*.cloudflare.com` | 443 | HTTPS/TCP | Infraestructura del túnel — recepción de mensajes |
| 4 | **Cloudflare Tunnel** | `*.trycloudflare.com` | 443 | HTTPS/TCP | URL pública del webhook (fase de pruebas) |
| 5 | **Cloudflare Tunnel** | `*.cfargotunnel.com` | 443 | HTTPS/TCP | URL del túnel nombrado (producción) |
| 6 | **Cloudflare Tunnel** | `cloudflareaccess.com` | 443 | HTTPS/TCP | Autenticación del túnel |
| 7 | **Cloudflare Tunnel** | `region1.v2.argotunnel.com` | 7844 | TCP+UDP | Canal de datos del túnel (mejor rendimiento) |
| 8 | **Cloudflare Tunnel** | `region2.v2.argotunnel.com` | 7844 | TCP+UDP | Canal de datos del túnel (redundancia) |

> **Nota puerto 7844:** Si está bloqueado, `cloudflared` usa automáticamente
> el puerto 443 como fallback. El sistema funciona en ambos casos, pero
> el puerto 7844 ofrece mejor rendimiento y latencia.

### 3.2 Reglas de MANTENIMIENTO (actualizaciones del sistema)

| # | Servicio | Host de destino | Puerto | Protocolo | Justificación |
|---|---|---|---|---|---|
| 9 | **GitHub** | `github.com` | 443 | HTTPS/TCP | Descarga de actualizaciones del código fuente |
| 10 | **GitHub objetos** | `objects.githubusercontent.com` | 443 | HTTPS/TCP | Descarga de archivos desde GitHub |
| 11 | **PyPI Python** | `pypi.org` | 443 | HTTPS/TCP | Instalación de dependencias del sistema |
| 12 | **PyPI archivos** | `files.pythonhosted.org` | 443 | HTTPS/TCP | Paquetes Python |

### 3.3 Reglas de MODELOS IA (solo instalación inicial — pueden ser temporales)

| # | Servicio | Host de destino | Puerto | Protocolo | Justificación |
|---|---|---|---|---|---|
| 13 | **Ollama** | `ollama.com` | 443 | HTTPS/TCP | Descarga del modelo de lenguaje local (LLM) |
| 14 | **Ollama Registry** | `registry.ollama.ai` | 443 | HTTPS/TCP | Descarga de modelos IA (qwen2.5, nomic-embed) |
| 15 | **HuggingFace** | `huggingface.co` | 443 | HTTPS/TCP | Descarga del modelo de reranking semántico |
| 16 | **HuggingFace CDN** | `cdn-lfs.huggingface.co` | 443 | HTTPS/TCP | Archivos de modelos HuggingFace |
| 17 | **HuggingFace CDN** | `cdn-lfs-us-1.huggingface.co` | 443 | HTTPS/TCP | Mirror de archivos HuggingFace |

> Una vez descargados los modelos (#13-17), estas reglas pueden desactivarse.
> Los modelos quedan almacenados permanentemente en el servidor.

### 3.4 Reglas OPCIONALES (monitoreo)

| # | Servicio | Host de destino | Puerto | Protocolo | Justificación |
|---|---|---|---|---|---|
| 18 | **Groq Console** | `console.groq.com` | 443 | HTTPS/TCP | Panel de monitoreo de uso de la API de IA |
| 19 | **WhatsApp Business** | `business.facebook.com` | 443 | HTTPS/TCP | Gestión del número de WhatsApp Business |

---

## 4. Resumen de Puertos

| Puerto | Protocolo | Cantidad de reglas | Servicios |
|---|---|---|---|
| **443** | HTTPS / TCP | 17 reglas | Todos los servicios externos |
| **7844** | TCP + UDP | 2 reglas | Cloudflare Tunnel (canal de datos) |

---

## 5. Diagrama de Flujo de Tráfico

```
                    INTERNET
                       │
         ┌─────────────▼──────────────┐
         │      Cloudflare CDN        │
         │  sara.sercop.gob.ec :443   │
         │  (TLS terminado aquí)      │
         └─────────────┬──────────────┘
                       │ Túnel cifrado
         ┌─────────────▼──────────────┐
         │   SERVIDOR app-bdd-chatbot │  ← Sin puertos de entrada abiertos
         │                            │
         │  cloudflared (túnel) :7844 │ ──SALIENTE──▶ Cloudflare
         │  SARA FastAPI        :8000 │ ──SALIENTE──▶ api.groq.com :443
         │  Ollama LLM          :11434│   (local)
         │  PostgreSQL          :5432 │   (local)
         └────────────────────────────┘
```

**No hay puertos de entrada abiertos.** El servidor inicia todas las conexiones.

---

## 6. Consideraciones de Seguridad

| Aspecto | Detalle |
|---|---|
| **Tráfico de entrada** | Ninguno — el servidor no acepta conexiones externas directas |
| **Cifrado** | Todo el tráfico usa TLS 1.2/1.3 (HTTPS) |
| **Certificado HTTPS** | Emitido y renovado automáticamente por Cloudflare, sin gestión manual |
| **Datos a Groq (EE.UU.)** | Las consultas de usuarios y fragmentos de normativa viajan a Groq para generar respuestas. Se recomienda validación con el área legal del SERCOP bajo normativa de protección de datos. |
| **Datos a Meta** | El texto de las respuestas viaja a Meta (WhatsApp Business). Es inherente al uso de la plataforma WhatsApp. |
| **Datos locales** | Base de datos PostgreSQL, modelos de IA (Ollama) y base de conocimiento normativa permanecen 100% en el servidor del SERCOP. |

---

## 7. Verificación Post-Implementación

Una vez aplicadas las reglas, ejecutar desde el servidor para confirmar conectividad:

```bash
cd /home/jonathan.ruiz/sara-sercop
python scripts/detectar_firewall.py --salida reporte_post_firewall.txt
```

El script prueba cada endpoint y genera un reporte con estado ✅/❌ por servicio.
Compartir el archivo `reporte_post_firewall.txt` con la Coordinación TIC para
confirmar que todas las reglas están activas.

---

## 8. Secuencia de Implementación Recomendada

```
Paso 1 — Equipo de Redes/Firewall
  └─ Aplicar reglas de firewall (sección 3)
  └─ Confirmar con: python scripts/detectar_firewall.py

Paso 2 — Coordinación TIC
  └─ Ejecutar: cloudflared tunnel create sara
  └─ Obtener el ID del túnel generado
  └─ Entregar ID al equipo de DNS

Paso 3 — Equipo de DNS
  └─ Crear registro CNAME: sara.sercop.gob.ec → <ID>.cfargotunnel.com
  └─ TTL: 300 segundos

Paso 4 — Coordinación TIC
  └─ Configurar el túnel para apuntar a localhost:8000
  └─ Verificar HTTPS: curl https://sara.sercop.gob.ec/
  └─ Registrar webhook en Meta: https://sara.sercop.gob.ec/webhook
  └─ Iniciar SARA en producción
```

---

## 9. Contacto

**Sistema:** SARA v2.0 — Asistente Virtual de Contratación Pública
**Servidor:** `app-bdd-chatbot` (Linux RHEL — infraestructura interna SERCOP)
**Responsable técnico:** Coordinación de TIC — SERCOP Ecuador
**Director TIC:** Paúl Vásquez Méndez
