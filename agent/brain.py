# agent/brain.py — Cerebro del agente Sercobot
#
# Pipeline de respuesta (en orden):
#   1. Shortcuts (0 tokens, 0ms) — 9 categorías detectadas por regex/keywords
#   2. Pre-tool execution — Python detecta intención y ejecuta tools sin LLM
#   3. RAG — recupera chunks normativa SERCOP
#   4. Cascada LLM secuencial: Groq → Claude Haiku → Gemini → Ollama local

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import unicodedata
import yaml
from functools import lru_cache
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

# ── Configuración de proveedores LLM ─────────────────────────────────────────

GROQ_API_KEY  = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL    = os.getenv("GROQ_MODEL_CASCADE", "llama-3.3-70b-versatile")
GROQ_URL      = "https://api.groq.com/openai/v1/chat/completions"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL      = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL   = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

OLLAMA_URL        = os.getenv("OLLAMA_BASE_URL", os.getenv("OLLAMA_URL", "http://localhost:11434"))
OLLAMA_MODEL      = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
OLLAMA_NUM_CTX    = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
OLLAMA_MAX_TOKENS = int(os.getenv("OLLAMA_MAX_TOKENS", "400"))

WIKI_DIR      = Path(os.getenv("WIKI_DIR", "knowledge/wiki"))
WIKI_FALLBACK = os.getenv("WIKI_FALLBACK", "true").lower() == "true"

# Orden de la cascada (configurable vía .env)
LLM_FALLBACK_ORDER = [p.strip() for p in
                      os.getenv("LLM_FALLBACK_ORDER", "groq,gemini,claude,local").split(",")]

# ── Patrón emoji ──────────────────────────────────────────────────────────────
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "]+",
    flags=re.UNICODE
)


# ─── Normalización ────────────────────────────────────────────────────────────

def _normalizar(texto: str) -> str:
    """Lowercase, sin tildes, strip."""
    texto = texto.lower().strip()
    texto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


_STOPWORDS = {_normalizar(w) for w in {
    "el", "la", "los", "las", "de", "que", "qué", "es", "un", "una",
    "me", "para", "en", "con", "a", "y", "o", "por", "del", "al",
    "le", "se", "mi", "tu", "su", "lo", "hay", "tiene", "tengo",
    "cual", "como", "cuando", "donde", "quien", "cuanto", "cuantos",
    # Verbos comunes sin contenido semántico en contratación
    "son", "ser", "esta", "estan", "esto", "ese", "esa",
    "aqui", "ahi", "si", "no", "mas", "muy", "bien", "solo",
    "puede", "puedo", "quiero", "necesito", "debo", "tengo",
    "hacer", "saber", "decir", "ver", "haber", "tener",
    # Términos genéricos del dominio que no distinguen entre FAQs
    "proceso", "procesos", "contrato", "contratos",
    "informacion", "informaciones",
    # Palabras interrogativas y verbos genéricos que no discriminan FAQs
    "existe", "existen", "cuales",
}}


def _tokens_sin_stopwords(texto_norm: str) -> set[str]:
    return {t for t in texto_norm.split() if t and t not in _STOPWORDS}


def _token_matches_keyword(qt: str, kw: str) -> bool:
    """True si query token y keyword son la misma palabra o una es prefijo de la otra (mín 4 chars)."""
    if qt == kw:
        return True
    min_len = min(len(qt), len(kw))
    if min_len < 4:
        return False
    return qt.startswith(kw) or kw.startswith(qt)


# ─── Palabras clave de shortcuts ─────────────────────────────────────────────

# Categoría 1 — Saludos
_KW_SALUDO = {_normalizar(w) for w in {
    "hola", "holaa", "holaaa", "hi", "hello", "hey",
    "buenas", "buenos dias", "buenas tardes", "buenas noches", "buen dia",
    "saludos", "saludos cordiales",
    "que tal", "como estas", "como esta", "como te va",
    "good morning", "good afternoon", "good evening",
    "inicio", "empezar", "comenzar", "start",
    "menu", "menú", "opciones", "ayuda", "help",
    "como funcionas", "que puedes hacer", "que haces", "como me ayudas",
    "informacion",
}}
_EMOJIS_SALUDO = {"👋", "🙋", "😊"}

# Categoría 2 — Despedidas
_KW_DESPEDIDA = {_normalizar(w) for w in {
    "adios", "bye", "chao", "chau", "hasta luego", "hasta pronto",
    "nos vemos", "hasta manana", "hasta la proxima", "me voy",
    "fue todo", "nada mas",
}}
_FRASES_DESPEDIDA = {_normalizar(w) for w in {
    "gracias y adios", "thank you", "thanks",
    "eso es todo", "con eso esta bien", "listo gracias", "ok gracias",
    "perfecto gracias", "fue todo", "nada mas",
}}
_EMOJIS_DESPEDIDA = {"🙏"}

# Categoría 3 — Agradecimientos (sin llegar a despedida)
_KW_AGRADECIMIENTO = {_normalizar(w) for w in {
    "gracias", "mil gracias", "muy amable", "excelente", "perfecto",
    "genial", "que bueno", "super", "chevere", "de nada", "con gusto",
    "estuvo bien", "muy bien",
}}
_EMOJIS_AGRADECIMIENTO = {"👍", "❤️", "🙌"}

# Categoría 4 — Afirmaciones
_KW_AFIRMACION = {_normalizar(w) for w in {
    "si", "ok", "okay", "dale", "claro", "exacto", "correcto",
    "asi es", "entendido", "de acuerdo", "bien", "esta bien",
    "por supuesto", "aja", "adelante",
}}
_EMOJIS_AFIRMACION = {"👍", "✅"}

# Categoría 5 — Negaciones
_KW_NEGACION = {_normalizar(w) for w in {
    "no", "nope", "nel", "para nada", "negativo", "no gracias",
    "no es eso", "tampoco", "ninguno",
}}
_EMOJIS_NEGACION = {"👎"}

# Categoría 6 — Confusión / Frustración
_KW_CONFUSION = {_normalizar(w) for w in {
    "no entiendo", "no me entiendes", "eso no es", "esta mal",
    "incorrecto", "no es correcto", "me confundiste", "repite",
    "otra vez", "de nuevo", "no es asi", "equivocado", "error",
}}
_EMOJIS_CONFUSION = {"😤", "😕", "🤔"}

# Scope keywords (stems para substring matching)
_SCOPE_STEMS = {_normalizar(w) for w in {
    "rup", "contrat", "proveedor", "licitac", "sie", "subast", "ofert",
    "adjudic", "pliego", "sercop", "losncp", "reglament", "resoluc",
    "menor cuantia", "infima", "cotizac", "consultor", "pac", "soce",
    "garantia", "anticip", "factura", "pago", "obra", "servici",
    "registr", "habilitac", "monto", "plazo", "compr", "public",
    "entidad", "proces", "procedimient", "normativ", "ley", "articul",
    "portal", "compraspublic", "catalog", "feria", "emergenci",
    "limpie", "construcc", "contratant", "adquisici", "compra",
    "convenio", "consorcio", "sancion", "puntaje", "pliego",
    "empate", "extranjero", "auditoria", "mantenimient",
    # Puja — solo en SIE
    "puja", "pujar",
    # Transparencia y denuncia (rendición de cuentas)
    "denuncia", "denunci", "corrupcion", "irregularid", "transparenc",
    "queja", "reclamo", "fiscaliz", "control", "veeduria",
    # Presupuesto y finanzas
    "presupuest", "referencial", "pie", "adjudicac",
    # Portal y acceso
    "usuario", "contraseña", "clave", "acceso", "portal",
    "pasivo", "activo", "estado",
    # Clasificador y capacitación
    "cpc", "clasificador", "examen", "certificac", "capacitac", "curso",
    # Conceptos nuevos LOSNCP vigente
    "valor por dinero", "mejor valor", "sostenibilid", "principio",
    "experiencia", "calific", "puntaje", "evaluac",
    "vae", "valor agregado", "ecuatorian",
    "tdr", "terminos de referencia", "especificacion",
    "convalidac", "orden de compra", "presupuestar",
    "pliegos", "convenio marco",
    "prorroga", "plazo", "extension",
    # Transferir a humano
    "asesor", "humano", "transferir", "agente",
    # Términos informales frecuentes
    "vender", "venta", "comprar", "cobrar", "pagar",
}}

# Mapa de respuestas al menú numerado
_MENU_QUERIES = {
    "1": "tipos contratacion",
    "2": "rup registro proveedor",
    "3": "buscar procesos portal",
    "4": "garantias tipos",
    "5": "losncp ley contratacion",
    "uno": "tipos contratacion",
    "dos": "rup registro proveedor",
    "tres": "buscar procesos portal",
    "cuatro": "garantias tipos",
    "cinco": "losncp ley contratacion",
    "opcion 1": "tipos contratacion",
    "opcion 2": "rup registro proveedor",
    "opcion 3": "buscar procesos portal",
    "opcion 4": "garantias tipos",
    "opcion 5": "losncp ley contratacion",
}

# Patrones de mensajes multimedia de WhatsApp
_MEDIA_PATTERNS = re.compile(
    r"^\s*\[?(audio|imagen|video|sticker|documento|gif|voz|voice|image|photo|picture)\]?\s*$",
    re.IGNORECASE,
)


# ─── Carga de configuración ───────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _cargar_config() -> dict:
    try:
        with open("config/prompts.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning("config/prompts.yaml no encontrado")
        return {}


@lru_cache(maxsize=1)
def _cargar_faq_cache() -> list[dict]:
    """Carga el FAQ cache al iniciar el servidor."""
    try:
        with open("config/faq_cache.yaml", "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        faqs = data.get("faqs", [])
        # Pre-normalizar keywords para comparación eficiente.
        # Se excluyen stopwords para evitar que palabras vacías como "que",
        # "es", "un" inflen el conteo de matches y produzcan falsos positivos.
        for faq in faqs:
            faq["_kw_norm"] = [
                _normalizar(str(kw))
                for kw in faq.get("keywords", [])
                if _normalizar(str(kw)) not in _STOPWORDS
            ]
        logger.info(f"[Sercobot] FAQ cache cargado: {len(faqs)} entradas")
        return faqs
    except FileNotFoundError:
        logger.warning("[Sercobot] config/faq_cache.yaml no encontrado — FAQ cache deshabilitado")
        return []


def _system_prompt() -> str:
    return _cargar_config().get(
        "system_prompt",
        "Eres Sercobot, asistente virtual del SERCOP Ecuador. "
        "Respondes preguntas sobre contratación pública citando siempre la normativa vigente.",
    )


# ─── Detección de shortcuts ───────────────────────────────────────────────────

def _es_vacio_o_solo_emoji(texto: str) -> bool:
    texto_strip = texto.strip()
    if not texto_strip:
        return True
    sin_emoji = _EMOJI_RE.sub("", texto_strip).strip()
    return not sin_emoji


def _contiene_emoji(texto: str, emojis: set[str]) -> bool:
    return any(e in texto for e in emojis)


def _contiene_kwset(texto_norm: str, kwset: set[str]) -> bool:
    return any(kw in texto_norm for kw in kwset)


def _coincide_exacto_token(texto_norm: str, kwset: set[str]) -> bool:
    """Verifica si algún token exacto del texto está en el kwset."""
    tokens = set(texto_norm.split())
    return bool(tokens & kwset)


def _es_fuera_scope(texto_norm: str) -> bool:
    """True si ningún stem de contratación está presente en el texto."""
    return not any(stem in texto_norm for stem in _SCOPE_STEMS)


def _check_faq(texto_norm: str) -> Optional[str]:
    """
    Busca coincidencia en el FAQ cache.

    Lógica de umbral adaptativa:
      - 1-2 keywords → todos deben estar presentes (100%)
      - 3+ keywords  → al menos 2 matches Y ≥50% del total

    Fallback para queries de 1 token: busca FAQ con coincidencia exacta
    o por prefijo (p.ej. "garantia" matchea keyword "garantias").
    """
    faqs = _cargar_faq_cache()
    mejor_score = 0.0
    mejor_matches = 0
    mejor_respuesta = None

    for faq in faqs:
        kw_norm = faq.get("_kw_norm", [])
        n = len(kw_norm)
        if not n:
            continue

        tokens_texto = set(texto_norm.split())
        matches = sum(1 for kw in kw_norm if kw in tokens_texto)
        ratio = matches / n

        # Umbral adaptativo
        if n <= 2:
            score = ratio if matches == n else 0.0   # exige todos
        else:
            score = ratio if (matches >= 2 and ratio >= 0.50) else 0.0

        # Mayor score gana; en empate, más matches absolutos gana
        if score > mejor_score or (score == mejor_score and score > 0 and matches > mejor_matches):
            mejor_score = score
            mejor_matches = matches
            mejor_respuesta = faq.get("respuesta", "")

    if mejor_score >= 0.50:
        return mejor_respuesta

    # ── Fallback para queries de 1 token significativo ────────────────────────
    # Cubre "rup", "pac", "sie", "puja", "garantias", "soce", etc. solos.
    # Usa coincidencia EXACTA para evitar falsos positivos como
    # "proceso" → "procesos" o "contrat" → "contratacion".
    q_tokens = _tokens_sin_stopwords(texto_norm)
    if len(q_tokens) == 1:
        qt = next(iter(q_tokens))
        if len(qt) >= 3:  # ignorar letras sueltas
            for faq in faqs:
                kw_norm = faq.get("_kw_norm", [])
                if qt in kw_norm:  # coincidencia exacta — no prefijos
                    return faq.get("respuesta", "").strip() or None

    return None


def _detectar_shortcut(mensaje: str) -> Optional[tuple[str, str]]:
    """
    Detecta el shortcut aplicable y retorna (categoria, respuesta) o None.
    Se ejecuta ANTES del RAG y los LLMs — 0 tokens, 0ms.
    """
    cfg = _cargar_config()
    texto_norm = _normalizar(mensaje)
    texto_strip = texto_norm.strip()
    es_corto = len(texto_norm.split()) <= 4

    # Cat 7 — Mensajes vacíos o solo emojis → menú
    if _es_vacio_o_solo_emoji(mensaje):
        return ("emoji_vacio", cfg.get("msg_bienvenida", ""))

    # Cat 7b — Mensajes multimedia (audio, imagen, sticker de WhatsApp)
    if _MEDIA_PATTERNS.match(mensaje.strip()):
        return ("media", cfg.get("msg_media", cfg.get("fallback_message", "")))

    # Cat 0 — Respuestas al menú numerado (1-5 / uno-cinco)
    if es_corto and texto_strip in _MENU_QUERIES:
        query_tema = _MENU_QUERIES[texto_strip]
        faq_resp = _check_faq(_normalizar(query_tema))
        if faq_resp:
            return ("faq_cache", faq_resp.strip())
        return ("saludo", cfg.get("msg_bienvenida", ""))

    # Cat 1 — Saludos → menú de bienvenida
    # es_corto evita que "ayuda en linea" / "help con proceso" disparen el menú
    if (
        (es_corto and _coincide_exacto_token(texto_norm, _KW_SALUDO))
        or _contiene_kwset(texto_norm, {_normalizar(f) for f in {
            "buenos dias", "buenas tardes", "buenas noches", "buen dia",
            "como estas", "como esta", "como te va", "que tal",
        }})
        or (es_corto and _contiene_emoji(mensaje, _EMOJIS_SALUDO))
    ):
        return ("saludo", cfg.get("msg_bienvenida", ""))

    # Cat 6 — Confusión/Frustración (antes que agradecimiento para "está mal" etc.)
    if (
        _contiene_kwset(texto_norm, _KW_CONFUSION)
        or (es_corto and _contiene_emoji(mensaje, _EMOJIS_CONFUSION))
    ):
        return ("confusion", cfg.get("msg_confusion", ""))

    # Cat 2 — Despedidas → mensaje de cierre
    if (
        _coincide_exacto_token(texto_norm, _KW_DESPEDIDA)
        or _contiene_kwset(texto_norm, _FRASES_DESPEDIDA)
        or (es_corto and _contiene_emoji(mensaje, _EMOJIS_DESPEDIDA))
    ):
        return ("despedida", cfg.get("msg_despedida", ""))

    # Cat 3 — Agradecimientos (solo mensajes cortos)
    if es_corto and (
        _coincide_exacto_token(texto_norm, _KW_AGRADECIMIENTO)
        or _contiene_emoji(mensaje, _EMOJIS_AGRADECIMIENTO)
    ):
        return ("agradecimiento", cfg.get("msg_agradecimiento", ""))

    # Cat 4 — Afirmaciones (solo mensajes cortos sin keywords de consulta)
    if es_corto and (
        _coincide_exacto_token(texto_norm, _KW_AFIRMACION)
        or (len(texto_norm.split()) == 1 and _contiene_emoji(mensaje, _EMOJIS_AFIRMACION))
    ) and _es_fuera_scope(texto_norm):
        return ("afirmacion", cfg.get("msg_afirmacion", ""))

    # Cat 5 — Negaciones (solo mensajes cortos sin keywords de consulta)
    if es_corto and (
        _coincide_exacto_token(texto_norm, _KW_NEGACION)
        or (len(texto_norm.split()) == 1 and _contiene_emoji(mensaje, _EMOJIS_NEGACION))
    ) and _es_fuera_scope(texto_norm):
        return ("negacion", cfg.get("msg_negacion", ""))

    # Cat 9 — FAQ cache hit
    # Umbral para menú genérico: < 2 tokens significativos.
    # Con 0-1 tokens la query es demasiado vaga para RAG.
    # Con ≥ 2 tokens (ej. "saca rup", "gana sie") hay contexto suficiente → RAG.
    # Si contiene al menos un stem SERCOP → intentar FAQ siempre, luego RAG.
    # Si no tiene scope → fuera_scope (tema ajeno al bot).
    _tokens_sig_sc = _tokens_sin_stopwords(texto_norm)
    if len(_tokens_sig_sc) < 2:
        if _es_fuera_scope(texto_norm):
            return ("fuera_scope", cfg.get("msg_fuera_scope", ""))
        faq_resp = _check_faq(texto_norm)
        if faq_resp:
            return ("faq_cache", faq_resp.strip())
        # 0-1 tokens con scope pero sin FAQ específico → menú
        return ("consulta_ambigua", cfg.get("msg_consulta_ambigua", cfg.get("msg_bienvenida", "")))
    else:
        faq_resp = _check_faq(texto_norm)
        if faq_resp:
            return ("faq_cache", faq_resp.strip())

    # Cat 8 — Fuera de scope → redirección
    if _es_fuera_scope(texto_norm):
        return ("fuera_scope", cfg.get("msg_fuera_scope", ""))

    return None  # → RAG + cascada LLM


# ─── Llamadas a LLMs (cascada) ───────────────────────────────────────────────

async def _llamar_groq(mensajes: list[dict]) -> str:
    """Groq API (llama-3.3-70b-versatile). Lanza excepción en fallo."""
    import httpx
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY no configurado")

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    MAX_USER_CHARS = int(os.getenv("GROQ_MAX_USER_CHARS", "6000"))
    mensajes_adj = []
    for i, msg in enumerate(mensajes):
        if msg["role"] == "user" and i == len(mensajes) - 1:
            content = msg["content"]
            if len(content) > MAX_USER_CHARS:
                content = content[:MAX_USER_CHARS] + "\n\n[contexto truncado]"
            mensajes_adj.append({"role": "user", "content": content})
        else:
            mensajes_adj.append(msg)

    payload = {
        "model": GROQ_MODEL,
        "messages": mensajes_adj,
        "temperature": 0.2,
        "max_tokens": 600,
    }
    async with httpx.AsyncClient(timeout=None) as client:
        response = await client.post(GROQ_URL, json=payload, headers=headers)

        if response.status_code == 413:
            # Reintentar sin contexto RAG
            solo_pregunta = mensajes_adj[-1]["content"].split("\n\n---\n")[0]
            payload["messages"] = [mensajes_adj[0], {"role": "user", "content": solo_pregunta}]
            response = await client.post(GROQ_URL, json=payload, headers=headers)

        if response.status_code == 429:
            raise RuntimeError(f"Groq 429: rate limit — {response.text[:200]}")

        response.raise_for_status()

        h = response.headers
        logger.debug(
            f"Groq límites — req: {h.get('x-ratelimit-remaining-requests','?')} | "
            f"tokens/min: {h.get('x-ratelimit-remaining-tokens','?')}"
        )
        data = response.json()
        return data["choices"][0]["message"]["content"]


async def _llamar_claude_haiku(mensajes: list[dict]) -> str:
    """Claude Haiku 4.5 vía Anthropic SDK."""
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY no configurado")

    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    system_content = next(
        (m["content"] for m in mensajes if m["role"] == "system"), ""
    )
    user_msgs = [m for m in mensajes if m["role"] != "system"]

    response = await client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=600,
        system=system_content,
        messages=user_msgs,
    )
    return response.content[0].text


async def _llamar_gemini(mensajes: list[dict]) -> str:
    """Gemini 2.5 Flash-Lite vía google-generativeai."""
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY no configurado")

    import google.generativeai as genai

    def _sync_gemini() -> str:
        genai.configure(api_key=GEMINI_API_KEY)
        system_instruction = None
        msgs_to_process = mensajes
        if mensajes and mensajes[0]["role"] == "system":
            system_instruction = mensajes[0]["content"]
            msgs_to_process = mensajes[1:]

        model = genai.GenerativeModel(
            model_name=GEMINI_MODEL,
            system_instruction=system_instruction,
        )

        history = []
        for msg in msgs_to_process[:-1]:
            role = "user" if msg["role"] == "user" else "model"
            history.append({"role": role, "parts": [msg["content"]]})

        last_content = msgs_to_process[-1]["content"] if msgs_to_process else ""
        chat = model.start_chat(history=history)
        resp = chat.send_message(last_content)
        return resp.text

    return await asyncio.to_thread(_sync_gemini)


async def _llamar_ollama(mensajes: list[dict]) -> str:
    """Ollama local (qwen2.5:3b). Último recurso de la cascada."""
    import httpx
    payload = {
        "model": OLLAMA_MODEL,
        "messages": mensajes,
        "stream": False,
        "options": {
            "temperature": 0.2,
            "top_p": 0.9,
            "num_ctx": OLLAMA_NUM_CTX,
            "num_predict": OLLAMA_MAX_TOKENS,
            "repeat_penalty": 1.1,
        },
    }
    async with httpx.AsyncClient(timeout=None) as client:
        response = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
        response.raise_for_status()
        data = response.json()
        return data.get("message", {}).get("content", "")


# Mapa proveedor → función + timeout
_PROVEEDORES: dict[str, tuple] = {
    "groq":   (_llamar_groq,         6.0),
    "claude": (_llamar_claude_haiku, 8.0),
    "gemini": (_llamar_gemini,       8.0),
    "local":  (_llamar_ollama,       45.0),
}


async def _cascade_llm(mensajes: list[dict]) -> tuple[str, str, float]:
    """
    Ejecuta la cascada de LLMs en orden configurado.
    Retorna (respuesta, proveedor_usado, tiempo_segundos).
    """
    fallidos: list[str] = []

    for nombre in LLM_FALLBACK_ORDER:
        if nombre not in _PROVEEDORES:
            logger.warning(f"[Sercobot] Proveedor desconocido en cascada: {nombre}")
            continue

        fn, timeout_s = _PROVEEDORES[nombre]
        t0 = time.time()
        try:
            resultado = await asyncio.wait_for(fn(mensajes), timeout=timeout_s)
            elapsed = time.time() - t0

            if fallidos:
                logger.info(
                    f"[Sercobot] {'+'.join(fallidos)} fallaron → {nombre}: {elapsed:.1f}s ✅"
                )
            else:
                logger.info(f"[Sercobot] {nombre}: {elapsed:.1f}s ✅")

            return resultado, nombre, elapsed

        except asyncio.TimeoutError:
            elapsed = time.time() - t0
            logger.warning(f"[Sercobot] {nombre} timeout {timeout_s}s → siguiente nivel")
            fallidos.append(nombre)

        except Exception as e:
            elapsed = time.time() - t0
            logger.warning(f"[Sercobot] {nombre} falló ({elapsed:.1f}s): {e} → siguiente nivel")
            fallidos.append(nombre)

    return "", "none", 0.0


# ─── Pre-tool execution ───────────────────────────────────────────────────────

def _detectar_tools(mensaje: str) -> list[tuple[str, dict]]:
    texto = mensaje.lower()
    tools_a_ejecutar: list[tuple[str, dict]] = []

    _kw_montos = [
        "monto", "umbral", "cuánto", "cuanto", "pie",
        "límite", "limite", "ínfima", "infima",
        "licitaci", "feria inclusiva",
        "cuánto puedo", "cuanto puedo", "hasta cuánto", "hasta cuanto",
        "valor máximo", "valor maximo",
    ]
    if any(kw in texto for kw in _kw_montos):
        tools_a_ejecutar.append(("obtener_montos_pie", {}))

    _kw_plazos = [
        "plazo", "días", "dias", "cuántos días", "cuantos dias",
        "impugn", "apelar", "apelaci", "recurso",
        "garantía", "garantia", "firmar contrato", "firma contrato",
        "cuándo firmar", "cuando firmar", "tiempo para", "cuánto tarda",
    ]
    if any(kw in texto for kw in _kw_plazos):
        if any(kw in texto for kw in ["impugn", "apelar", "apelaci", "recurso de apelaci"]):
            tipo_plazo = "impugnacion"
        elif any(kw in texto for kw in ["garantía", "garantia", "fiel cumplimiento", "anticipo"]):
            tipo_plazo = "garantias"
        elif any(kw in texto for kw in ["firma", "firmar contrato", "suscripci"]):
            tipo_plazo = "contrato"
        elif any(kw in texto for kw in ["sie", "subasta inversa", "puja"]):
            tipo_plazo = "subasta_inversa"
        elif any(kw in texto for kw in ["licitaci"]):
            tipo_plazo = "licitacion"
        else:
            tipo_plazo = "contrato"
        tools_a_ejecutar.append(("obtener_plazos", {"tipo": tipo_plazo}))

    _kw_rup = [
        "rup", "proveedor", "registrar", "registro único", "registro de proveedor",
        "habilitado", "habilitarme", "proveedores del estado", "contratar con el estado",
        "cómo ser proveedor", "como ser proveedor", "requisitos para ser proveedor",
        "inscribir", "inscripción",
    ]
    if any(kw in texto for kw in _kw_rup):
        tools_a_ejecutar.append(("info_rup", {}))

    _kw_fecha = [
        "fecha", "hora", "hoy es", "qué día", "que dia",
        "qué hora", "que hora", "día de hoy", "dia de hoy",
    ]
    if any(kw in texto for kw in _kw_fecha):
        tools_a_ejecutar.append(("obtener_fecha_hora_ecuador", {}))

    _kw_tipo = [
        "qué proceso", "que proceso", "qué tipo", "que tipo",
        "qué procedimiento", "que procedimiento", "cómo contrato", "como contrato",
        "qué modalidad", "que modalidad", "cuál proceso", "cual proceso",
        "debo usar", "debo contratar", "mejor proceso",
    ]
    if any(kw in texto for kw in _kw_tipo):
        tools_a_ejecutar.append(("recomendar_tipo_contratacion", {"descripcion": mensaje}))

    return tools_a_ejecutar


def _formatear_resultado_tool(nombre: str, resultado_json: str) -> str:
    try:
        data = json.loads(resultado_json)
    except Exception:
        return resultado_json

    if nombre == "obtener_montos_pie":
        anio = data.get("anio", "")
        pie = data.get("pie", 0)
        lineas = [f"## Umbrales de contratación {anio} (PIE: ${pie:,.0f})"]
        for k, v in data.items():
            if isinstance(v, dict) and "usd" in v:
                usd = v["usd"]
                norma = v.get("normativa", "")
                nombre_tipo = k.replace("_", " ").title()
                usd_str = f"${usd:,.0f}" if isinstance(usd, int) else str(usd)
                lineas.append(f"- {nombre_tipo}: {usd_str} USD ({norma})")
        if "advertencia" in data:
            lineas.append(f"\nNota: {data['advertencia']}")
        return "\n".join(lineas)

    elif nombre == "obtener_plazos":
        tipo = data.get("tipo", "")
        plazos = data.get("plazos", {})
        norma = plazos.pop("normativa", "") if isinstance(plazos, dict) else ""
        nota = plazos.pop("nota", "") if isinstance(plazos, dict) else ""
        lineas = [f"## Plazos: {tipo.replace('_', ' ').title()}"]
        for k, v in (plazos.items() if isinstance(plazos, dict) else []):
            lineas.append(f"- {k.replace('_', ' ').title()}: {v}")
        if norma:
            lineas.append(f"- Normativa: {norma}")
        if nota:
            lineas.append(f"- Nota: {nota}")
        return "\n".join(lineas)

    elif nombre == "info_rup":
        lineas = ["## Registro Único de Proveedores (RUP)"]
        lineas.append(f"- {data.get('descripcion', '')}")
        lineas.append(f"- Normativa: {data.get('normativa', '')}")
        lineas.append(f"- Costo: {data.get('costo', '')}")
        lineas.append(f"- Tiempo: {data.get('tiempo_proceso', '')}")
        lineas.append(f"- Renovación: {data.get('renovacion', '')}")
        req_pn = data.get("requisitos_persona_natural", [])
        if req_pn:
            lineas.append("- Requisitos persona natural: " + "; ".join(req_pn))
        req_pj = data.get("requisitos_persona_juridica", [])
        if req_pj:
            lineas.append("- Requisitos persona jurídica: " + "; ".join(req_pj))
        lineas.append(f"- Portal: {data.get('portal', '')}")
        return "\n".join(lineas)

    elif nombre == "obtener_fecha_hora_ecuador":
        return (
            f"## Fecha y hora actual en Ecuador\n"
            f"- Fecha: {data.get('fecha', '')}\n"
            f"- Hora: {data.get('hora', '')} (UTC-5)\n"
            f"- Día: {data.get('dia_semana', '')}"
        )

    elif nombre == "recomendar_tipo_contratacion":
        lineas = [f"## Proceso recomendado: {data.get('tipo_recomendado', '')}"]
        lineas.append(f"- {data.get('descripcion', '')}")
        lineas.append(f"- Montos: {data.get('montos', '')}")
        lineas.append(f"- Normativa: {data.get('normativa', '')}")
        lineas.append(f"- Ventaja: {data.get('ventaja', '')}")
        return "\n".join(lineas)

    return resultado_json


# ─── Wiki local (fallback sin pgvector) ──────────────────────────────────────

def _buscar_en_wiki(query: str, max_chars: int = 2000) -> str:
    if not WIKI_DIR.exists():
        return ""
    query_lower = query.lower()
    terminos = [t for t in query_lower.split() if len(t) > 3]
    if not terminos:
        return ""
    mejores: list[tuple[int, str, str]] = []
    for md_file in WIKI_DIR.glob("*.md"):
        contenido = md_file.read_text(encoding="utf-8", errors="replace")
        hits = sum(contenido.lower().count(t) for t in terminos)
        if hits > 0:
            mejores.append((hits, md_file.stem, contenido))
    if not mejores:
        return ""
    mejores.sort(reverse=True)
    _, nombre, contenido = mejores[0]
    return f"[WIKI: {nombre.replace('_', ' ').title()}]\n{contenido[:max_chars]}"


# ─── Logging de consultas (async, fire-and-forget) ───────────────────────────

async def _log_consulta(
    pregunta: str,
    respuesta: str,
    proveedor_llm: str,
    tiempo_ms: int,
    fue_shortcut: bool,
    shortcut_tipo: Optional[str],
    rag_chunks: int,
    telefono: str = "",
) -> None:
    try:
        from agent.memory import registrar_consulta
        pregunta_norm = _normalizar(pregunta)
        await registrar_consulta(
            pregunta=pregunta,
            pregunta_normalizada=pregunta_norm,
            respuesta=respuesta,
            proveedor_llm=proveedor_llm,
            tiempo_ms=tiempo_ms,
            fue_shortcut=fue_shortcut,
            shortcut_tipo=shortcut_tipo,
            rag_chunks=rag_chunks,
            telefono=telefono,
        )
    except Exception as e:
        logger.debug(f"[Sercobot] No se pudo registrar consulta: {e}")


# ─── Pipeline principal ───────────────────────────────────────────────────────

async def generar_respuesta(
    mensaje: str,
    historial: list[dict],
    telefono: str = "",
) -> str:
    """
    Pipeline completo:
      1. Shortcuts (9 categorías) — 0 tokens, 0ms
      2. Pre-tool execution (montos, plazos, RUP, fecha, tipo)
      3. RAG sobre sercop_db
      4. Cascada LLM: Groq → Claude Haiku → Gemini → Ollama local
    """
    t_inicio = time.time()

    if not mensaje or len(mensaje.strip()) < 1:
        return _cargar_config().get("fallback_message", "¿En qué puedo ayudarte?")

    # ── 1. Sistema de shortcuts ───────────────────────────────────────────────
    shortcut = _detectar_shortcut(mensaje)
    if shortcut:
        categoria, respuesta = shortcut
        elapsed_ms = int((time.time() - t_inicio) * 1000)
        logger.info(f"[Sercobot] Shortcut: {categoria} ({elapsed_ms}ms, 0 tokens)")
        asyncio.ensure_future(_log_consulta(
            pregunta=mensaje, respuesta=respuesta,
            proveedor_llm="shortcut", tiempo_ms=elapsed_ms,
            fue_shortcut=True, shortcut_tipo=categoria,
            rag_chunks=0, telefono=telefono,
        ))
        return respuesta

    # ── 1b. Consulta demasiado corta — pedir clarificación ───────────────────
    # Solo si llegó aquí (shortcut no aplica) y tiene < 2 tokens significativos.
    # Con ≥ 2 tokens hay contexto suficiente para RAG ("saca rup", "gana sie").
    _tokens_sig = _tokens_sin_stopwords(_normalizar(mensaje))
    if len(_tokens_sig) < 2:
        cfg = _cargar_config()
        respuesta_menu = cfg.get("msg_consulta_ambigua", cfg.get("msg_bienvenida", ""))
        elapsed_ms = int((time.time() - t_inicio) * 1000)
        logger.info(f"[Sercobot] Query corta ({len(_tokens_sig)} tokens) → menú ({elapsed_ms}ms)")
        asyncio.ensure_future(_log_consulta(
            pregunta=mensaje, respuesta=respuesta_menu,
            proveedor_llm="shortcut", tiempo_ms=elapsed_ms,
            fue_shortcut=True, shortcut_tipo="consulta_ambigua",
            rag_chunks=0, telefono=telefono,
        ))
        return respuesta_menu

    # ── 2. Construir contexto ────────────────────────────────────────────────
    system = _system_prompt()
    mensajes_base: list[dict] = [{"role": "system", "content": system}]
    for msg in historial:
        mensajes_base.append({"role": msg["role"], "content": msg["content"]})

    bloques_contexto: list[str] = []
    num_chunks = 0

    # Pre-tool execution — retorna directo sin LLM si hay resultado
    tools_detectadas = _detectar_tools(mensaje)
    if tools_detectadas:
        from agent.tools import ejecutar_tool
        bloques_tool: list[str] = []
        for nombre_tool, argumentos in tools_detectadas:
            try:
                resultado_json = ejecutar_tool(nombre_tool, argumentos)
                bloque = _formatear_resultado_tool(nombre_tool, resultado_json)
                bloques_tool.append(bloque)
                logger.info(f"Pre-tool ejecutada: {nombre_tool}")
            except Exception as e:
                logger.warning(f"Error en pre-tool {nombre_tool}: {e}")

        if bloques_tool:
            respuesta = "\n\n".join(bloques_tool)
            elapsed_ms = int((time.time() - t_inicio) * 1000)
            logger.info(f"[Sercobot] Tool directo: {elapsed_ms}ms, 0 tokens")
            asyncio.ensure_future(_log_consulta(
                pregunta=mensaje, respuesta=respuesta,
                proveedor_llm="tool_directo", tiempo_ms=elapsed_ms,
                fue_shortcut=True, shortcut_tipo="tool_directo",
                rag_chunks=0, telefono=telefono,
            ))
            return respuesta

    # RAG
    if not bloques_contexto:
        try:
            from agent.retriever import recuperar_contexto_formateado
            contexto_rag, num_chunks = await recuperar_contexto_formateado(mensaje)
            if num_chunks > 0:
                bloques_contexto.append(
                    f"## Normativa relevante (fuentes SERCOP)\n{contexto_rag}"
                )
                logger.info(f"RAG: {num_chunks} chunks recuperados")
        except Exception as e:
            logger.warning(f"RAG no disponible: {e}")

    # Wiki fallback
    if not bloques_contexto and WIKI_FALLBACK:
        wiki = _buscar_en_wiki(mensaje)
        if wiki:
            bloques_contexto.append(wiki)
            logger.info("Wiki local usada como fallback")

    # Mensaje enriquecido
    if bloques_contexto:
        contexto_texto = "\n\n".join(bloques_contexto)
        mensaje_enriquecido = (
            f"{mensaje}\n\n"
            "---\n"
            "Información de referencia para responder:\n\n"
            f"{contexto_texto}"
        )
    else:
        mensaje_enriquecido = mensaje

    mensajes_base.append({"role": "user", "content": mensaje_enriquecido})

    # ── 3. Cascada LLM ────────────────────────────────────────────────────────
    respuesta, proveedor_usado, t_llm = await _cascade_llm(mensajes_base)

    if not respuesta:
        respuesta = _cargar_config().get(
            "error_message_tecnico",
            "Estoy teniendo dificultades técnicas en este momento. "
            "Por favor intenta en unos minutos o llama al 1800-737267. 😔",
        )
        proveedor_usado = "none"

    elapsed_ms = int((time.time() - t_inicio) * 1000)
    asyncio.ensure_future(_log_consulta(
        pregunta=mensaje, respuesta=respuesta,
        proveedor_llm=proveedor_usado, tiempo_ms=elapsed_ms,
        fue_shortcut=False, shortcut_tipo=None,
        rag_chunks=num_chunks, telefono=telefono,
    ))

    return respuesta
