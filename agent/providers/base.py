# agent/providers/base.py — Clase base para proveedores de WhatsApp
# Generado por AgentKit

"""
Define la interfaz común que todos los proveedores de WhatsApp deben implementar.
Esto permite cambiar de proveedor sin modificar el resto del código.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from fastapi import Request


@dataclass
class MensajeEntrante:
    """Mensaje normalizado — mismo formato sin importar el proveedor."""
    telefono: str       # Número del remitente
    texto: str          # Contenido del mensaje
    mensaje_id: str     # ID único del mensaje
    es_propio: bool     # True si lo envió el agente (se ignora)


class ProveedorWhatsApp(ABC):
    """Interfaz que cada proveedor de WhatsApp debe implementar."""

    @abstractmethod
    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """Extrae y normaliza mensajes del payload del webhook."""
        ...

    @abstractmethod
    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """Envía un mensaje de texto. Retorna True si fue exitoso."""
        ...

    async def validar_webhook(self, request: Request) -> dict | int | None:
        """Verificación GET del webhook (solo Meta la requiere). Retorna respuesta o None."""
        return None

    async def enviar_documento(self, telefono: str, url_documento: str,
                                nombre_archivo: str, caption: str = "") -> bool:
        """Envía un documento. Implementado solo en Meta por ahora."""
        return False

    async def enviar_lista_interactiva(
        self,
        telefono: str,
        cuerpo: str,
        opciones: list[dict],
        encabezado: str = "",
        pie: str = "",
        boton_texto: str = "Ver opciones",
    ) -> bool:
        """Envía una lista interactiva. Default: no soportado."""
        return False

    async def enviar_botones_interactivos(
        self,
        telefono: str,
        cuerpo: str,
        botones: list[dict],
        encabezado: str = "",
        pie: str = "",
    ) -> bool:
        """Envía botones de respuesta rápida. Default: no soportado."""
        return False
