# agent/providers/meta.py — Adaptador para Meta WhatsApp Cloud API
# Generado por AgentKit

from __future__ import annotations

import os
import logging
import httpx
from fastapi import Request
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante

logger = logging.getLogger("agentkit")


class ProveedorMeta(ProveedorWhatsApp):
    """Proveedor de WhatsApp usando la API oficial de Meta (Cloud API)."""

    def __init__(self):
        self.access_token = os.getenv("META_ACCESS_TOKEN")
        self.phone_number_id = os.getenv("META_PHONE_NUMBER_ID")
        self.verify_token = os.getenv("META_VERIFY_TOKEN", "World2026")
        self.api_version = "v21.0"

    async def validar_webhook(self, request: Request) -> dict | int | None:
        """Meta requiere verificación GET con hub.verify_token."""
        params = request.query_params
        mode = params.get("hub.mode")
        token = params.get("hub.verify_token")
        challenge = params.get("hub.challenge")
        if mode == "subscribe" and token == self.verify_token:
            # Meta espera el challenge como respuesta en texto plano
            return int(challenge)
        return None

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """
        Parsea el payload anidado de Meta Cloud API.
        Soporta:
          - text: mensaje de texto normal
          - interactive.list_reply: selección de una lista interactiva
          - interactive.button_reply: clic en botón de respuesta rápida
        """
        try:
            body = await request.json()
        except Exception:
            return []
        mensajes = []
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    tipo = msg.get("type")
                    telefono = msg.get("from", "")
                    mensaje_id = msg.get("id", "")

                    if tipo == "text":
                        mensajes.append(MensajeEntrante(
                            telefono=telefono,
                            texto=msg.get("text", {}).get("body", ""),
                            mensaje_id=mensaje_id,
                            es_propio=False,
                        ))
                    elif tipo == "interactive":
                        inter = msg.get("interactive", {})
                        sub = inter.get("type")
                        if sub == "list_reply":
                            reply_id = inter.get("list_reply", {}).get("id", "")
                            mensajes.append(MensajeEntrante(
                                telefono=telefono,
                                texto=reply_id,
                                mensaje_id=mensaje_id,
                                es_propio=False,
                            ))
                        elif sub == "button_reply":
                            reply_id = inter.get("button_reply", {}).get("id", "")
                            mensajes.append(MensajeEntrante(
                                telefono=telefono,
                                texto=reply_id,
                                mensaje_id=mensaje_id,
                                es_propio=False,
                            ))
        return mensajes

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """Envía mensaje via Meta WhatsApp Cloud API."""
        if not self.access_token or not self.phone_number_id:
            logger.warning("META_ACCESS_TOKEN o META_PHONE_NUMBER_ID no configurados")
            return False
        url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": telefono,
            "type": "text",
            "text": {"body": mensaje},
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, headers=headers)
            if r.status_code != 200:
                logger.error(f"Error Meta API: {r.status_code} — {r.text}")
            return r.status_code == 200

    async def enviar_lista_interactiva(
        self,
        telefono: str,
        cuerpo: str,
        opciones: list[dict],
        encabezado: str = "",
        pie: str = "",
        boton_texto: str = "Ver opciones",
    ) -> bool:
        """
        Envía un mensaje de lista interactiva (List Message) de WhatsApp Cloud API.
        Hasta 10 opciones — cada una con id, titulo (24 char) y descripcion (72 char).
        Útil para reemplazar menús "responde 1, 2, 3...".
        """
        if not self.access_token or not self.phone_number_id:
            logger.warning("META_ACCESS_TOKEN o META_PHONE_NUMBER_ID no configurados")
            return False
        url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        rows = []
        for op in opciones[:10]:
            row = {
                "id": str(op.get("id", ""))[:200],
                "title": str(op.get("titulo", ""))[:24],
            }
            if op.get("descripcion"):
                row["description"] = str(op["descripcion"])[:72]
            rows.append(row)

        interactive = {
            "type": "list",
            "body": {"text": cuerpo[:1024]},
            "action": {
                "button": boton_texto[:20],
                "sections": [{"title": "Opciones", "rows": rows}],
            },
        }
        if encabezado:
            interactive["header"] = {"type": "text", "text": encabezado[:60]}
        if pie:
            interactive["footer"] = {"text": pie[:60]}

        payload = {
            "messaging_product": "whatsapp",
            "to": telefono,
            "type": "interactive",
            "interactive": interactive,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, json=payload, headers=headers)
            if r.status_code != 200:
                logger.error(f"Error Meta API lista: {r.status_code} — {r.text}")
            return r.status_code == 200

    async def enviar_botones_interactivos(
        self,
        telefono: str,
        cuerpo: str,
        botones: list[dict],
        encabezado: str = "",
        pie: str = "",
    ) -> bool:
        """
        Envía un mensaje con botones de respuesta rápida (max 3 botones).
        Cada botón: {id, titulo (max 20 char)}.
        """
        if not self.access_token or not self.phone_number_id:
            return False
        url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        buttons = [
            {
                "type": "reply",
                "reply": {
                    "id": str(b.get("id", ""))[:256],
                    "title": str(b.get("titulo", ""))[:20],
                },
            }
            for b in botones[:3]
        ]
        interactive = {
            "type": "button",
            "body": {"text": cuerpo[:1024]},
            "action": {"buttons": buttons},
        }
        if encabezado:
            interactive["header"] = {"type": "text", "text": encabezado[:60]}
        if pie:
            interactive["footer"] = {"text": pie[:60]}
        payload = {
            "messaging_product": "whatsapp",
            "to": telefono,
            "type": "interactive",
            "interactive": interactive,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, json=payload, headers=headers)
            if r.status_code != 200:
                logger.error(f"Error Meta API botones: {r.status_code} — {r.text}")
            return r.status_code == 200

    async def enviar_documento(self, telefono: str, url_documento: str,
                                nombre_archivo: str, caption: str = "") -> bool:
        """Envía un documento PDF via Meta WhatsApp Cloud API."""
        if not self.access_token or not self.phone_number_id:
            logger.warning("META_ACCESS_TOKEN o META_PHONE_NUMBER_ID no configurados")
            return False
        url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": telefono,
            "type": "document",
            "document": {
                "link": url_documento,
                "filename": nombre_archivo,
            },
        }
        if caption:
            payload["document"]["caption"] = caption
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, headers=headers)
            if r.status_code != 200:
                logger.error(f"Error Meta API documento: {r.status_code} — {r.text}")
            return r.status_code == 200
