# Correo — Estado de Pruebas SARA y Próximos Pasos

---

**Para:** Ing. Paúl Vásquez Méndez
**Cargo:** Director de Infraestructura y Operaciones — Coordinación TIC, SERCOP
**De:** Jonathan Ruiz
**Asunto:** Estado de avance — Sistema SARA: pruebas completadas en servidor, pendiente validación WhatsApp Business
**Fecha:** Abril 2026

---

Estimado Ing. Vásquez Méndez,

Me permito informarle el estado actual del sistema SARA y los pasos pendientes
para su puesta en producción con WhatsApp Business.

---

**Avance completado**

Las pruebas técnicas del sistema han sido realizadas satisfactoriamente
directamente en el servidor de producción (`app-bdd-chatbot`). Los resultados
obtenidos son los siguientes:

- El asistente responde consultas normativas en **1 a 2 segundos**, con citación
  correcta de artículos de la LOSNCP, el Reglamento General y resoluciones vigentes.
- La base de conocimiento contiene **3,059 fragmentos** indexados de 17 documentos
  oficiales del SERCOP.
- El sistema identifica correctamente tipos de procesos, montos del PIE 2026,
  plazos, requisitos del RUP y otros datos estructurados de contratación pública.
- El túnel Cloudflare ha sido instalado en el servidor y está listo para recibir
  el tráfico del webhook de WhatsApp.

---

**Limitación encontrada durante las pruebas**

La validación del webhook con la plataforma **Meta WhatsApp Business** no pudo
realizarse desde mi estación de trabajo debido a las restricciones de red
(firewall / Citrix) que aplican actualmente en la red institucional del SERCOP.

Esta validación consiste en registrar la URL pública del servidor como punto de
recepción de mensajes de WhatsApp. Para completarla se requiere:

1. Que el servidor tenga acceso saliente a los dominios de Meta y Cloudflare
   (detallados en el informe técnico adjunto).
2. Disponer de un subdominio institucional (`sara.sercop.gob.ec`) o en su defecto
   autorización para registrar un dominio externo para este fin.
3. Acceso al panel de **Meta WhatsApp Business Manager** para registrar el webhook.

---

**Solicitud**

Para completar la puesta en producción, solicito su gestión ante los equipos
correspondientes para:

| # | Acción | Equipo responsable |
|---|---|---|
| 1 | Apertura de reglas de firewall / Citrix (listado adjunto) | Equipo de Seguridad y Redes |
| 2 | Creación del registro DNS `sara.sercop.gob.ec` | Equipo de DNS |
| 3 | Acceso al panel Meta WhatsApp Business Manager | Coordinación TIC / quien administre la cuenta |

El detalle técnico completo de cada solicitud — dominios, puertos, protocolos
y justificación — se encuentra en el documento adjunto:
**`informe_direccion_TIC.md`**

---

**Tiempo estimado para producción**

Una vez habilitada la infraestructura de red, la puesta en producción completa
se estima en **1 día hábil**.

Quedo a su disposición para cualquier consulta o reunión técnica que requiera.

Atentamente,

**Jonathan Ruiz**
Coordinación de Tecnología de la Información y Comunicaciones
SERCOP Ecuador
