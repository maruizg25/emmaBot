# agent/tools.py — Herramientas del agente SERCOP

"""
Funciones utilitarias disponibles para el agente SERCOP.
Complementan el RAG con lógica de dominio específica.
"""

from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger("agentkit")


# ─── Tipos de contratación ────────────────────────────────────────────────────

TIPOS_CONTRATACION = {
    "catalogo_electronico": {
        "nombre": "Catálogo Electrónico",
        "descripcion": "Adquisición de bienes y servicios normalizados disponibles en el catálogo del SERCOP.",
        "montos": "Sin límite de monto para bienes y servicios normalizados.",
        "normativa": "Art. 43 LOSNCP",
        "ventaja": "Proceso más ágil — no requiere concurso, precio ya negociado.",
    },
    "licitacion": {
        "nombre": "Licitación",
        "descripcion": "Para contratos de gran monto. Proceso público y competitivo.",
        "montos": "Superior al 0.000015 del PIE para bienes/servicios; superior al 0.00003 del PIE para obras.",
        "normativa": "Art. 48 LOSNCP",
        "ventaja": "Máxima transparencia y competencia.",
    },
    "subasta_inversa": {
        "nombre": "Subasta Inversa Electrónica",
        "descripcion": "Para bienes y servicios normalizados. Los proveedores compiten bajando precios en tiempo real.",
        "montos": "Sin límite de monto (cuando no está en catálogo electrónico).",
        "normativa": "Art. 47 LOSNCP",
        "ventaja": "Precios más bajos para el Estado por competencia en tiempo real.",
    },
    "infima_cuantia": {
        "nombre": "Ínfima Cuantía",
        "descripcion": "Para adquisición de bienes, servicios (incluida consultoría) y obras de monto reducido que no consten en el Catálogo Electrónico.",
        "montos": "Igual o inferior a USD $10,000 (monto fijo — Art. 50 LOSNCP).",
        "normativa": "Art. 50 LOSNCP",
        "ventaja": "Máxima agilidad — sin proceso precontractual formal. No puede usarse para subdividir contratos.",
    },
    "contratacion_directa": {
        "nombre": "Contratación Directa",
        "descripcion": "Contratación de consultoría de monto reducido con un solo proveedor.",
        "montos": "Hasta el 0.000002 del PIE para consultoría.",
        "normativa": "Art. 40 LOSNCP",
        "ventaja": "Simplicidad para consultorías pequeñas.",
    },
    "regimen_especial": {
        "nombre": "Régimen Especial",
        "descripcion": "Para situaciones específicas: seguridad, defensa, comunicación social, asesoría legal, etc.",
        "montos": "Variable según el caso.",
        "normativa": "Art. 2 LOSNCP y Reglamento correspondiente",
        "ventaja": "Flexibilidad para necesidades especiales del Estado.",
    },
    "feria_inclusiva": {
        "nombre": "Feria Inclusiva",
        "descripcion": "Para adquisición de bienes y servicios a actores de la Economía Popular y Solidaria (EPS), artesanos y MIPYMES.",
        "montos": "Montos reducidos definidos en la normativa vigente.",
        "normativa": "Art. 51 LOSNCP",
        "ventaja": "Inclusión económica y social de pequeños productores y MIPYMES.",
    },
}


def obtener_tipo_contratacion(tipo_key: str) -> dict | None:
    """Retorna información detallada de un tipo de contratación."""
    return TIPOS_CONTRATACION.get(tipo_key.lower().replace(" ", "_"))


def listar_tipos_contratacion() -> list[dict]:
    """Lista todos los tipos de contratación con su info básica."""
    return [
        {"clave": k, "nombre": v["nombre"], "normativa": v["normativa"]}
        for k, v in TIPOS_CONTRATACION.items()
    ]


def recomendar_tipo_contratacion(descripcion: str) -> str:
    """
    Orientación básica sobre qué tipo de contratación aplica
    basándose en palabras clave de la descripción.
    """
    desc = descripcion.lower()

    if any(k in desc for k in ["urgente", "emergencia", "inmediato"]):
        return "infima_cuantia"
    if any(k in desc for k in ["catálogo", "normalizado", "estandarizado"]):
        return "catalogo_electronico"
    if any(k in desc for k in ["obra", "construcción", "infraestructura"]):
        return "licitacion"
    if any(k in desc for k in ["consultoría", "asesoría", "estudio"]):
        return "contratacion_directa"
    if any(k in desc for k in ["economía popular", "eps", "artesanos", "pequeños"]):
        return "feria_inclusiva"
    if any(k in desc for k in ["seguridad", "defensa", "comunicación social"]):
        return "regimen_especial"
    return "subasta_inversa"  # Default para bienes/servicios no normalizados


# ─── RUP (Registro Único de Proveedores) ────────────────────────────────────

def info_rup() -> dict:
    """Información sobre el Registro Único de Proveedores."""
    return {
        "descripcion": "El RUP es el registro oficial de proveedores habilitados para contratar con el Estado ecuatoriano.",
        "normativa": "Art. 16-18 LOSNCP",
        "portal": "https://www.compraspublicas.gob.ec",
        "requisitos_persona_natural": [
            "Cédula de ciudadanía o pasaporte",
            "RUC activo y al día con obligaciones tributarias",
            "Estar al día con el IESS (afiliaciones)",
            "Correo electrónico activo",
        ],
        "requisitos_persona_juridica": [
            "RUC de la empresa activo",
            "Escritura de constitución",
            "Nombramiento del representante legal vigente",
            "Estar al día con obligaciones tributarias y del IESS",
        ],
        "renovacion": "Anual — en enero de cada año",
        "costo": "Gratuito",
        "tiempo_proceso": "24-48 horas hábiles tras validación de documentos",
        "suspension": "Por incumplimientos contractuales, deudas con el Estado, o inhabilitación",
    }


# ─── Plazos referenciales ─────────────────────────────────────────────────────

PLAZOS_REFERENCIALES = {
    "subasta_inversa": {
        "publicacion_convocatoria": "Mínimo 5 días hábiles antes del evento",
        "preguntas_y_aclaraciones": "Hasta 2 días antes del evento",
        "puja": "60 minutos",
        "adjudicacion": "3 días hábiles tras la puja",
    },
    "licitacion": {
        "publicacion": "30 días hábiles",
        "consultas": "10 días hábiles",
        "ofertas": "30 días hábiles",
        "calificacion": "15 días hábiles",
        "adjudicacion": "5 días hábiles",
    },
    "impugnacion": {
        "recurso_de_apelacion": "3 días hábiles desde la notificación de adjudicación (Art. 102 LOSNCP)",
        "resolucion_recurso": "7 días hábiles desde la interposición",
        "accion_contencioso_administrativa": "90 días desde que se agoten recursos administrativos",
        "nota": "El recurso de apelación suspende el proceso hasta su resolución.",
        "normativa": "Art. 102-103 LOSNCP; Art. 305 RGLOSNCP",
    },
    "contrato": {
        "firma_contrato": "Máximo 15 días hábiles tras la adjudicación",
        "anticipo": "Hasta 30% del monto contractual, entregado tras garantía de buen uso",
        "plazo_ejecucion": "Definido en pliegos según el objeto contractual",
        "acta_entrega_recepcion": "Provisional y definitiva según reglamento",
        "normativa": "Art. 69-71 LOSNCP; Art. 290-294 RGLOSNCP",
    },
    "garantias": {
        "fiel_cumplimiento": "5% del valor del contrato (contratos > 0.000002 PIE)",
        "buen_uso_anticipo": "100% del anticipo recibido",
        "tecnica": "5% del valor ofertado (en licitación y cotización)",
        "vigencia": "Igual al plazo del contrato + 60 días adicionales",
        "devolucion_fiel_cumplimiento": "Tras acta de entrega-recepción definitiva",
        "normativa": "Art. 73-77 LOSNCP; Art. 274-284 RGLOSNCP",
    },
}


def obtener_plazos(tipo: str) -> dict:
    """Retorna plazos referenciales para un tipo de contratación."""
    return PLAZOS_REFERENCIALES.get(tipo.lower().replace(" ", "_"), {})


# ─── Montos PIE por año ───────────────────────────────────────────────────────

# Fuente: Presupuesto General del Estado aprobado por la Asamblea Nacional.
# Los montos en USD son fijos para cada año y se publican en el Registro Oficial.
# Si el año no está en el diccionario, retornar el más reciente.

MONTOS_PIE: dict[int, dict] = {
    2025: {
        "infima_cuantia":       {"usd": 10_000,  "normativa": "Art. 50 LOSNCP", "descripcion": "Monto fijo — igual o inferior a USD $10,000"},
        "licitacion_bienes":    {"porcentaje": ">0.000015", "usd": ">$544,725", "normativa": "Art. 48 LOSNCP"},
        "licitacion_obras":     {"porcentaje": ">0.00003",  "usd": ">$1,089,450", "normativa": "Art. 48 LOSNCP"},
        "contratacion_directa": {"porcentaje": "0.000002",  "usd": 72_630,   "normativa": "Art. 40 LOSNCP"},
        "nota": "Menor cuantía y cotización eliminados por reforma LOSNCP octubre 2025. Ver Art. 47 (SIE), Art. 48 (Licitación), Art. 50 (Ínfima cuantía), Art. 51 (Feria Inclusiva).",
    },
    2026: {
        "infima_cuantia":       {"usd": 10_000,  "normativa": "Art. 50 LOSNCP", "descripcion": "Monto fijo — igual o inferior a USD $10,000"},
        "licitacion_bienes":    {"porcentaje": ">0.000015", "usd": ">$555,000", "normativa": "Art. 48 LOSNCP"},
        "licitacion_obras":     {"porcentaje": ">0.00003",  "usd": ">$1,110,000", "normativa": "Art. 48 LOSNCP"},
        "contratacion_directa": {"porcentaje": "0.000002",  "usd": 74_000,   "normativa": "Art. 40 LOSNCP"},
        "nota": "Menor cuantía y cotización eliminados por reforma LOSNCP octubre 2025. Ver Art. 47 (SIE), Art. 48 (Licitación), Art. 50 (Ínfima cuantía), Art. 51 (Feria Inclusiva).",
    },
}


def obtener_montos_pie(anio: int | None = None) -> dict:
    """
    Retorna los umbrales de contratación en USD para el año solicitado.
    Si no se especifica el año, retorna los del año en curso.
    """
    from datetime import datetime
    if anio is None:
        anio = datetime.now().year
    # Si no tenemos el año exacto, usar el más reciente disponible
    if anio not in MONTOS_PIE:
        anio = max(MONTOS_PIE.keys())
    data = MONTOS_PIE[anio].copy()
    data["anio"] = anio
    data["advertencia"] = (
        "Montos referenciales basados en el PIE aprobado. "
        "Verificar el valor oficial vigente en www.compraspublicas.gob.ec"
    )
    return data


# ─── Utilidades generales ─────────────────────────────────────────────────────

def obtener_fecha_hora_ecuador() -> dict:
    """Fecha y hora actual en Ecuador (UTC-5)."""
    from datetime import timezone, timedelta
    tz_ecuador = timezone(timedelta(hours=-5))
    ahora = datetime.now(tz_ecuador)
    return {
        "fecha": ahora.strftime("%d de %B de %Y"),
        "hora": ahora.strftime("%H:%M"),
        "dia_semana": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"][ahora.weekday()],
    }


CONTACTO_SERCOP = {
    "portal":    "https://www.compraspublicas.gob.ec",
    "web":       "https://www.sercop.gob.ec",
    "telefono":  "1800-SERCOP (1800-737267)",
    "email":     "info@sercop.gob.ec",
    "direccion": "Av. Amazonas N37-57 y Unión Nacional de Periodistas, Quito, Ecuador",
    "horario":   "Lunes a viernes, 08h00 a 17h00",
}


# ─── Descriptores JSON Schema para tool calling (Ollama / Gemma 4) ────────────

TOOLS_SCHEMA: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "recomendar_tipo_contratacion",
            "description": (
                "Recomienda el procedimiento de contratación pública correcto según el monto "
                "y el tipo de bien o servicio. Usar cuando el usuario mencione un monto específico "
                "o describa qué quiere contratar."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "descripcion": {
                        "type": "string",
                        "description": (
                            "Descripción del bien, servicio u obra a contratar. "
                            "Ejemplo: 'limpieza de oficinas', 'construcción de vereda', 'consultoría legal'"
                        ),
                    },
                    "monto": {
                        "type": "number",
                        "description": "Monto estimado en dólares USD. Ejemplo: 5000, 80000, 200000",
                    },
                },
                "required": ["descripcion"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "obtener_plazos",
            "description": (
                "Retorna los plazos referenciales para procedimientos de contratación, "
                "impugnaciones, contratos y garantías. Usar cuando el usuario pregunte "
                "por tiempos, plazos, días, cuándo firmar, impugnar, apelar o garantías."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tipo": {
                        "type": "string",
                        "description": "Tipo de proceso. Valores: subasta_inversa, licitacion, feria_inclusiva, impugnacion, contrato, garantias",
                        "enum": ["subasta_inversa", "licitacion", "feria_inclusiva", "impugnacion", "contrato", "garantias"],
                    },
                },
                "required": ["tipo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "info_rup",
            "description": (
                "Retorna información completa sobre el Registro Único de Proveedores (RUP): "
                "requisitos, costo, plazos y proceso de registro. Usar cuando el usuario pregunte "
                "sobre el RUP, cómo registrarse como proveedor, o requisitos para contratar con el Estado."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "obtener_fecha_hora_ecuador",
            "description": (
                "Retorna la fecha y hora actual en Ecuador (UTC-5). "
                "Usar cuando el usuario pregunte la fecha, hora o día actual."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "obtener_montos_pie",
            "description": (
                "Retorna los umbrales de contratación en dólares USD para el año vigente "
                "según el Presupuesto Inicial del Estado (PIE). "
                "Usar cuando el usuario pregunte cuánto es el monto para ínfima cuantía, "
                "licitación, feria inclusiva, o cuánto dinero corresponde a cada proceso. "
                "Nota: menor cuantía y cotización fueron eliminados por la reforma LOSNCP octubre 2025."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "anio": {
                        "type": "integer",
                        "description": "Año fiscal. Si no se especifica, se usa el año actual. Ejemplo: 2025, 2026",
                    },
                },
                "required": [],
            },
        },
    },
]


# ─── Dispatcher — ejecuta la función por nombre ───────────────────────────────

def ejecutar_tool(nombre: str, argumentos: dict) -> str:
    """
    Ejecuta una tool por su nombre y retorna el resultado como string JSON.
    Llamado por brain.py cuando Gemma emite un tool_call.
    """
    import json

    try:
        if nombre == "recomendar_tipo_contratacion":
            descripcion = argumentos.get("descripcion", "")
            monto = argumentos.get("monto")
            tipo_key = recomendar_tipo_contratacion(descripcion)
            info = obtener_tipo_contratacion(tipo_key) or {}
            resultado = {
                "tipo_recomendado": info.get("nombre", tipo_key),
                "descripcion": info.get("descripcion", ""),
                "montos": info.get("montos", ""),
                "normativa": info.get("normativa", ""),
                "ventaja": info.get("ventaja", ""),
            }
            if monto:
                resultado["monto_consultado"] = f"${monto:,.0f} USD"
            return json.dumps(resultado, ensure_ascii=False)

        elif nombre == "obtener_plazos":
            tipo = argumentos.get("tipo", "")
            plazos = obtener_plazos(tipo)
            if not plazos:
                return json.dumps({"error": f"No se encontraron plazos para: {tipo}"})
            return json.dumps({"tipo": tipo, "plazos": plazos}, ensure_ascii=False)

        elif nombre == "info_rup":
            return json.dumps(info_rup(), ensure_ascii=False)

        elif nombre == "obtener_fecha_hora_ecuador":
            return json.dumps(obtener_fecha_hora_ecuador(), ensure_ascii=False)

        elif nombre == "obtener_montos_pie":
            anio = argumentos.get("anio")
            return json.dumps(obtener_montos_pie(anio), ensure_ascii=False)

        else:
            return json.dumps({"error": f"Tool desconocida: {nombre}"})

    except Exception as e:
        return json.dumps({"error": str(e)})
