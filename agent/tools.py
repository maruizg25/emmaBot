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
        "descripcion": "Compra directa de bienes y servicios normalizados registrados en convenios marco del SERCOP.",
        "montos": "Sin límite de monto.",
        "normativa": "Art. 43 LOSNCP",
        "ventaja": "Proceso más ágil — orden de compra directa, sin concurso.",
    },
    "subasta_inversa": {
        "nombre": "Subasta Inversa Electrónica (SIE)",
        "descripcion": "Para bienes y servicios NO catalogados, cuando el precio más bajo y el mercado competitivo representen el mejor valor por dinero. Los proveedores pujan a la baja en tiempo real.",
        "montos": "Sin umbral en USD — se aplica cuando bienes/servicios son estandarizados, hay mercado competitivo y el precio refleja por sí solo el mejor valor por dinero (Art. 74 RGLOSNCP).",
        "normativa": "Art. 47 LOSNCP; Art. 74 RGLOSNCP",
        "ventaja": "Precios más bajos por competencia en tiempo real. Si hay una sola oferta calificada, se hace sesión de negociación.",
    },
    "licitacion": {
        "nombre": "Licitación",
        "descripcion": "Para bienes, obras y servicios (excepto consultoría) cuando la SIE no sea el procedimiento idóneo. Para obras siempre se usa Licitación.",
        "montos": "Sin umbral fijo en USD — se aplica cuando deben priorizarse atributos distintos al precio (calidad, sostenibilidad, innovación, costos del ciclo de vida). Obras: siempre Licitación (Art. 74 RGLOSNCP).",
        "normativa": "Art. 48 LOSNCP; Art. 74 RGLOSNCP",
        "ventaja": "Máxima transparencia y evaluación integral (no solo precio).",
    },
    "infima_cuantia": {
        "nombre": "Ínfima Cuantía",
        "descripcion": "Para bienes, servicios (incluida consultoría) y obras cuya cuantía sea igual o inferior a USD $10,000, que no consten en el Catálogo Electrónico.",
        "montos": "Igual o inferior a USD $10,000 (monto fijo).",
        "normativa": "Art. 50 LOSNCP",
        "ventaja": "Máxima agilidad. No puede usarse para subdividir contratos ni como contratación constante y recurrente.",
    },
    "feria_inclusiva": {
        "nombre": "Feria Inclusiva",
        "descripcion": "Para bienes y servicios de producción nacional y origen local, no catalogados. Solo participan organizaciones de la EPS, artesanos, emprendedores, negocios populares, agricultura familiar campesina, micro y pequeñas empresas.",
        "montos": "Sin umbral fijo en USD — definido por normativa del SERCOP.",
        "normativa": "Art. 51 LOSNCP",
        "ventaja": "Inclusión económica y social de productores nacionales y locales.",
    },
    "concurso_publico_consultoria": {
        "nombre": "Concurso Público de Consultoría",
        "descripcion": "Para contratar servicios de consultoría. Los criterios de selección se definen en el Reglamento.",
        "montos": "Según plazos y términos del Reglamento conforme el presupuesto referencial.",
        "normativa": "Art. 42 LOSNCP",
        "ventaja": "Selección basada en calidad técnica, experiencia y propuesta metodológica.",
    },
    "regimen_especial": {
        "nombre": "Régimen Especial",
        "descripcion": "Para situaciones específicas: seguridad, defensa, comunicación social, emergencia, etc. Puede gestionarse también por ínfima cuantía si no supera USD $10,000.",
        "montos": "Variable según el caso.",
        "normativa": "Art. 2 LOSNCP y Reglamento correspondiente",
        "ventaja": "Flexibilidad para necesidades especiales del Estado.",
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


def recomendar_tipo_contratacion(descripcion: str, monto: float | None = None) -> str:
    """
    Orientación básica sobre qué tipo de contratación aplica.
    Basado en Art. 17 RGLOSNCP (procedimientos) y Art. 74 RGLOSNCP (mejor valor por dinero).
    """
    desc = descripcion.lower()

    if monto is not None and monto <= 10_000:
        return "infima_cuantia"
    if any(k in desc for k in ["catálogo", "catalogado", "convenio marco"]):
        return "catalogo_electronico"
    if any(k in desc for k in ["obra", "construcción", "infraestructura", "edificio"]):
        return "licitacion"  # Obras: siempre licitación (Art. 74 RGLOSNCP)
    if any(k in desc for k in ["consultoría", "asesoría", "estudio técnico"]):
        return "concurso_publico_consultoria"
    if any(k in desc for k in ["economía popular", "eps", "artesanos", "pequeños", "mipymes", "inclusiv"]):
        return "feria_inclusiva"
    if any(k in desc for k in ["seguridad", "defensa", "comunicación social", "emergencia"]):
        return "regimen_especial"
    return "subasta_inversa"  # Default: bienes/servicios estandarizados (Art. 74 RGLOSNCP)


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
        "fiel_cumplimiento": "5% del valor del contrato",
        "buen_uso_anticipo": "100% del anticipo recibido",
        "tecnica": "5% del valor ofertado (en licitación)",
        "vigencia": "Igual al plazo del contrato + 60 días adicionales",
        "devolucion_fiel_cumplimiento": "Tras acta de entrega-recepción definitiva",
        "normativa": "Art. 73-77 LOSNCP; Art. 274-284 RGLOSNCP",
    },
}


def obtener_plazos(tipo: str) -> dict:
    """Retorna plazos referenciales para un tipo de contratación."""
    return PLAZOS_REFERENCIALES.get(tipo.lower().replace(" ", "_"), {})


# ─── Umbrales de contratación (LOSNCP vigente — reforma octubre 2025) ────────
#
# CAMBIO FUNDAMENTAL: la LOSNCP vigente ya NO usa coeficientes del PIE para
# determinar SIE vs Licitación. La selección se basa en el principio de
# "mejor valor por dinero" (Art. 74 RGLOSNCP).
#
# Solo ínfima cuantía tiene un monto fijo en la ley: USD $10,000 (Art. 50).
# Los procedimientos de régimen común (Art. 17 RGLOSNCP) son:
#   1. Catálogo Electrónico
#   2. Subasta Inversa Electrónica (SIE)
#   3. Licitación
#   4. Concurso Público de Consultoría
#   5. Feria Inclusiva
#   6. Ínfima Cuantía

UMBRALES_CONTRATACION = {
    "infima_cuantia": {
        "usd": 10_000,
        "normativa": "Art. 50 LOSNCP",
        "descripcion": "Igual o inferior a USD $10,000 — monto fijo en la ley.",
    },
    "seleccion_sie_vs_licitacion": {
        "normativa": "Art. 74 RGLOSNCP",
        "descripcion": (
            "No hay umbral en USD. La selección entre SIE y Licitación se basa en el "
            "análisis de mejor valor por dinero: si bienes/servicios son estandarizados, "
            "existe mercado competitivo y el precio refleja el mejor valor → SIE. "
            "En caso contrario → Licitación. Obras → siempre Licitación."
        ),
    },
    "feria_inclusiva": {
        "normativa": "Art. 51 LOSNCP",
        "descripcion": "Sin umbral fijo — bienes y servicios de producción nacional y origen local, no catalogados. Solo proveedores EPS, artesanos, MIPYMES.",
    },
    "catalogo_electronico": {
        "normativa": "Art. 43 LOSNCP",
        "descripcion": "Sin límite de monto — bienes y servicios normalizados de convenios marco.",
    },
    "nota": (
        "La LOSNCP vigente (reforma octubre 2025) eliminó menor cuantía y cotización. "
        "Ya NO se usan coeficientes del PIE para determinar el procedimiento. "
        "Procedimientos vigentes: Catálogo Electrónico, SIE, Licitación, "
        "Concurso Público de Consultoría, Feria Inclusiva, Ínfima Cuantía (Art. 17 RGLOSNCP)."
    ),
}


def obtener_montos_pie(anio: int | None = None) -> dict:
    """
    Retorna los umbrales de contratación vigentes.
    Nota: la LOSNCP vigente ya no usa coeficientes del PIE para SIE/Licitación.
    """
    data = UMBRALES_CONTRATACION.copy()
    data["advertencia"] = (
        "La LOSNCP vigente (octubre 2025) cambió la lógica de selección de procedimientos. "
        "Ya no se usan porcentajes del PIE. Verificar en www.compraspublicas.gob.ec"
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
                "Retorna los umbrales de contratación vigentes según la LOSNCP (reforma octubre 2025). "
                "IMPORTANTE: la ley ya NO usa coeficientes del PIE para SIE/Licitación. "
                "Solo ínfima cuantía tiene monto fijo: USD $10,000 (Art. 50). "
                "La selección entre SIE y Licitación se basa en mejor valor por dinero (Art. 74 RGLOSNCP). "
                "Menor cuantía y cotización fueron eliminados."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
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
            tipo_key = recomendar_tipo_contratacion(descripcion, monto)
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
