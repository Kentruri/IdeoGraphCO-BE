"""Limpieza de texto de artículos scrapeados.

Remueve "frases parásito", firmas de periodistas, emails, CTAs,
footers de marca, cookie banners, paywalls y otros fragmentos
que no son contenido editorial.

NO convierte a minúsculas (el modelo ConfliBERT es Cased).
"""

import re
import unicodedata

# ---------------------------------------------------------------------------
# Patrones de "frases parásito" (CTAs, referencias cruzadas)
# ---------------------------------------------------------------------------

# Bloque completo: frase + línea vacía + título de otra noticia
_CTA_BLOCK_PATTERN = re.compile(
    r"(?:LEA TAMBIÉN|Lea también|Lea:|Le puede interesar|Le recomendamos|"
    r"También le puede interesar|Puede leer:|Siga leyendo|"
    r"Ver más:|Ver también:|Leer más:|"
    r"Más noticias|Noticias relacionadas|Siga el minuto a minuto:?)"
    r"\s*\n\s*\n?"
    r"[^\n]{0,200}\n?",
    re.MULTILINE,
)

# Frases sueltas sin bloque (a veces aparecen inline)
_CTA_INLINE_PATTERN = re.compile(
    r"(?:LEA TAMBIÉN|Lea también|Lea:|Le puede interesar|Le recomendamos|"
    r"También le puede interesar|Puede leer:|Siga leyendo|"
    r"Ver más:|Ver también:|Leer más:):?\s*",
)

# ---------------------------------------------------------------------------
# CTAs de redes sociales y WhatsApp
# ---------------------------------------------------------------------------

_SOCIAL_CTA_PATTERN = re.compile(
    r"(?:"
    r"Haga clic aquí para seguirnos en WhatsApp"
    r"|Si desean? inscribirse"
    r"|Queremos tener una comunicación más directa"
    r"|Únete a nuestro canal de WhatsApp"
    r"|Síguenos en WhatsApp"
    r")[^\n]*\n?",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Cookie banners (versión completa)
# ---------------------------------------------------------------------------

# Bloque completo: desde "En este portal utilizamos..." hasta el final del párrafo
_COOKIE_BANNER_PATTERN = re.compile(
    r"En este portal utilizamos datos de navegación.*?"
    r"(?:aceptando esta utilización\.?|Puede conocer cómo deshabilitarlas[^\n]*|aquí)",
    re.DOTALL,
)

# Restos del cookie banner cuando trafilatura captura solo el final
_COOKIE_BANNER_REMNANT_PATTERN = re.compile(
    r"(?:^|\n)\s*"
    r"Puede conocer cómo deshabilitarlas u obtener más información\s*\n"
    r"\s*aquí\s*\n",
    re.IGNORECASE,
)

# Restos sueltos de UI de cookies/legales
_UI_REMNANT_PATTERN = re.compile(
    r"^\s*(?:"
    r"aquí"  # palabra "aquí" sola en una línea
    r"|Aceptar(?:\s+y\s+continuar)?"
    r"|Continuar\s+sin\s+aceptar"
    r"|Configuración\s+de\s+cookies"
    r"|Política\s+de\s+privacidad"
    r"|Términos\s+y\s+condiciones"
    r")\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# ---------------------------------------------------------------------------
# Paywalls / suscripción
# ---------------------------------------------------------------------------

_PAYWALL_PATTERN = re.compile(
    r"(?:"
    r"Ya tienes una cuenta vinculada a [A-Z\s]+,\s*por favor inicia sesión[^\n]*"
    r"|¡Hola! Parece que has alcanzado tu límite[^\n]*"
    r"|¿Quieres seguir disfrutando de este y otros beneficios[^\n]*"
    r"|Adquiere el plan de suscripción[^\n]*"
    r"|¡Hola! Haz? excedido el máximo de peticiones[^\n]*"
    r"|Para más información continua navegando en[^\n]*"
    r")",
)

# Mensajes de error y UI de chatbot
_UI_NOISE_PATTERN = re.compile(
    r"(?:"
    r"Error \d{3}\s*\n[^\n]*"
    r"|Estamos resolviendo el problema[^\n]*"
    r"|Procesando tu pregunta[^\n]*"
    r"|¿Sabías que registrándote en nuestro portal[^\n]*"
    r"|Con el envío de tus consultas, aceptas los Términos[^\n]*"
    r"|Recuerda que las respuestas generadas pueden presentar inexactitudes[^\n]*"
    r"|De acuerdo con las políticas de la IA[^\n]*"
    r"|no es posible responder a las preguntas relacionadas[^\n]*"
    r")",
)

# ---------------------------------------------------------------------------
# Firmas de periodistas al final del artículo
# ---------------------------------------------------------------------------

_SIGNATURE_PATTERN = re.compile(
    r"\n\s*"
    r"(?:"
    r"[A-ZÁÉÍÓÚÑ\s]{5,50}\s*\n\s*Periodista\s+de\s+[^\n]{1,80}\.?"
    r"|Redacción\s+[^\n]{1,50}\.?"
    r")"
    r"\s*$",
    re.MULTILINE,
)

# Emails al final del texto
_EMAIL_TAIL_PATTERN = re.compile(
    r"\n\s*[\w.+-]+@[\w-]+\.[\w.]+\s*$",
    re.MULTILINE,
)

# ---------------------------------------------------------------------------
# Footers de marca (PORTAFOLIO, etc.)
# ---------------------------------------------------------------------------

_BRAND_FOOTER_PATTERN = re.compile(
    r"\n\s*(?:PORTAFOLIO|EL TIEMPO|EL ESPECTADOR|SEMANA)\s*$",
    re.MULTILINE,
)

# ---------------------------------------------------------------------------
# Listings de noticias relacionadas (cuando trafilatura captura mal el artículo)
# ---------------------------------------------------------------------------

_SECTION_CUTOFF_PATTERN = re.compile(
    r"\n\s*"
    r"(?:Más para ver|Nuestro mundo|Más noticias|Lo más leído|Lo último|"
    r"Más sobre|También en [A-Z]|Tendencias|En portada|Otras noticias|"
    r"Horóscopo\s*\n|Crucigrama\s*\n|"
    r"BOLETINES EL TIEMPO|EL TIEMPO GOOGLE NEWS|EL TIEMPO WHATSAPP|"
    r"EL TIEMPO APP|SUSCRÍBETE AL DIGITAL|"
    r"Sigue toda la información de [A-Z][^\n]{1,50} en Facebook|"
    r"Conforme a los criterios de\s*$)"
    r".*$",
    re.DOTALL,
)

# Marcador de inicio de artículo (El Tiempo prefija con "Noticia\n")
_ARTICLE_START_MARKER = re.compile(
    r"\A.*?\n\s*"
    r"(?:Noticia|Análisis|Opinión|Editorial|Reportaje|Crónica|Entrevista|"
    r"Exclusivo suscriptores)\s*\n",
    re.DOTALL,
)

# ---------------------------------------------------------------------------
# Líneas cortas típicas de UI residual del scraping
# ---------------------------------------------------------------------------
# Patrones CONSERVADORES: solo matchean líneas que son CLARAMENTE UI, no
# contenido. Un filtro genérico por longitud sería peligroso (podría borrar
# titulares cortos o citas).

_UI_SHORT_LINES = re.compile(
    r"^\s*(?:"
    r"Compartir|Guardar|Comentar|Imprimir|"
    r"Reportar\s+(?:un\s+)?error|"
    r"Suscríbete|Suscribirse|Recibir\s+alertas|"
    r"\d+\s*min(?:utos)?\s+de\s+lectura|"
    r"Foto[\s:]+[^\n]{0,80}|"           # "Foto: AFP" — pie corto, sin pie largo de fotonota
    r"Cr[eé]ditos?[\s:]+[^\n]{0,80}|"
    r"AFP|EFE|Reuters|Colprensa"        # créditos de agencia sueltos
    r")\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# ---------------------------------------------------------------------------
# URLs residuales (trafilatura normalmente las quita, pero a veces quedan)
# ---------------------------------------------------------------------------
_URL_PATTERN = re.compile(r"https?://\S+|www\.\S+")

# ---------------------------------------------------------------------------
# Handles de redes sociales al FINAL del texto
# ---------------------------------------------------------------------------
# Inline (@petrogustavo respondió...) puede ser contenido relevante.
# Solo eliminamos handles que están sueltos al final, típicos de bylines.
_HANDLE_TAIL_PATTERN = re.compile(
    r"\n\s*@[\w_]+\s*$",
    re.MULTILINE,
)

# ---------------------------------------------------------------------------
# Limpieza de espacios en blanco excesivos
# ---------------------------------------------------------------------------

_MULTI_NEWLINES = re.compile(r"\n{3,}")
_TRAILING_SPACES = re.compile(r"[ \t]+$", re.MULTILINE)


def _safe_section_cutoff(text: str, pattern: re.Pattern, min_position: float = 0.5) -> str:
    """Aplica un cutoff solo si el match ocurre después de `min_position` del texto.

    El _SECTION_CUTOFF_PATTERN original usa `.*$` con DOTALL: si la frase de corte
    aparece por accidente en la primera mitad del artículo, borra todo el cuerpo
    útil. Esta función mitiga ese riesgo: si el match está antes del 50% del texto,
    se considera falso positivo y se ignora.
    """
    match = pattern.search(text)
    if match and match.start() > len(text) * min_position:
        return text[:match.start()]
    return text


def _dynamic_brand_footer(source_name: str) -> re.Pattern:
    """Footer dinámico para una fuente específica (en mayúsculas, al final).

    Permite limpiar marcas que no están en `_BRAND_FOOTER_PATTERN`, sin tener
    que editar la lista cada vez que añadimos un medio nuevo.
    """
    escaped = re.escape(source_name.upper())
    return re.compile(rf"\n\s*{escaped}\s*$", re.MULTILINE)


def _remove_author_lines(text: str, authors: list[str]) -> str:
    """Remueve líneas que solo contienen el nombre de un autor."""
    if not authors:
        return text
    lines = text.split("\n")
    cleaned = []
    author_names = {a.strip().lower() for a in authors if a.strip()}
    for line in lines:
        stripped = line.strip().lower()
        if stripped and stripped in author_names:
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def clean_article_text(
    text: str,
    *,
    source_name: str | None = None,
    authors: list[str] | None = None,
) -> str:
    """Limpia el texto de un artículo scrapeado.

    Args:
        text: texto del artículo (puede contener basura post-trafilatura).
        source_name: nombre del medio (ej. "lasillavacia"); si se da, se genera
            un footer dinámico para esa marca además de los hardcoded.
        authors: lista de autores; sus nombres sueltos en una línea se eliminan.

    Aplica en orden:
    0. Normalización Unicode (NFKC) — convierte \\xa0 → espacio normal, etc.
    1. Marcador de inicio: si hay "Noticia\\n"/"Análisis\\n", descartar todo lo previo
    2. Cookie banners (completo + restos + restos de UI legal)
    3. Paywalls y UI de chatbot
    4. Bloques CTA + frases inline + redes sociales
    5. URLs residuales
    6. UI shorts (Compartir, Foto:, etc.)
    7. Firmas, emails, handles y footers al final
    8. Footer dinámico de la fuente (si source_name)
    9. Section cutoff seguro (solo si el match está en la mitad final)
    10. Líneas con solo el nombre del autor
    11. Normalización de espacios
    """
    # 0. Normalización Unicode: \xa0 → " ", caracteres compatibility, etc.
    text = unicodedata.normalize("NFKC", text)

    # 1. Marcador de inicio — SOLO si el marcador aparece cerca del comienzo.
    #    El patrón original (\A.*? con DOTALL) borraba toda la primera mitad
    #    del artículo si una línea suelta "Entrevista"/"Opinión" aparecía en
    #    medio del cuerpo.
    start_match = _ARTICLE_START_MARKER.match(text)
    if start_match and start_match.end() <= 300:
        text = text[start_match.end():]

    # 2. Cookie banners
    text = _COOKIE_BANNER_PATTERN.sub("", text)
    text = _COOKIE_BANNER_REMNANT_PATTERN.sub("\n", text)
    text = _UI_REMNANT_PATTERN.sub("", text)

    # 3. Paywall y UI de chatbot
    text = _PAYWALL_PATTERN.sub("", text)
    text = _UI_NOISE_PATTERN.sub("", text)

    # 4. CTAs (bloques + inline + redes)
    text = _CTA_BLOCK_PATTERN.sub("", text)
    text = _CTA_INLINE_PATTERN.sub("", text)
    text = _SOCIAL_CTA_PATTERN.sub("", text)

    # 5. URLs residuales (trafilatura normalmente las quita pero a veces no)
    text = _URL_PATTERN.sub("", text)

    # 6. UI shorts (Compartir, Foto: pie corto, créditos de agencia)
    text = _UI_SHORT_LINES.sub("", text)

    # 7. Firmas, emails, handles, footers al final
    text = _SIGNATURE_PATTERN.sub("", text)
    text = _EMAIL_TAIL_PATTERN.sub("", text)
    text = _HANDLE_TAIL_PATTERN.sub("", text)
    text = _BRAND_FOOTER_PATTERN.sub("", text)

    # 8. Footer dinámico por fuente (si tenemos el nombre)
    if source_name:
        text = _dynamic_brand_footer(source_name).sub("", text)

    # 9. Section cutoff: solo si el match está en la 2da mitad del texto
    #    (evita borrar todo si "Tendencias" aparece por accidente en el cuerpo)
    text = _safe_section_cutoff(text, _SECTION_CUTOFF_PATTERN, min_position=0.5)

    # 10. Nombres de autores sueltos
    if authors:
        text = _remove_author_lines(text, authors)

    # 11. Normalizar espacios
    text = _TRAILING_SPACES.sub("", text)
    text = _MULTI_NEWLINES.sub("\n\n", text)
    text = text.strip()

    return text
