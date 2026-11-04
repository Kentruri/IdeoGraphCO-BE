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
    r"(?:LEA TAMBIÉN|Lea también|Lea:|Lea aquí|Lea además|Le puede interesar|"
    r"Le recomendamos|También le puede interesar|Puede leer:|Siga leyendo|"
    r"Ver más:|Ver también:|Vea también|Vea aquí|Leer más:|Consulte aquí|"
    r"Más noticias|Noticias relacionadas|Siga el minuto a minuto:?)"
    r"\s*\n\s*\n?"
    r"[^\n]{0,200}\n?",
    re.MULTILINE | re.IGNORECASE,   # los sitios varían la capitalización
)

# Frases sueltas sin bloque (a veces aparecen inline)
_CTA_INLINE_PATTERN = re.compile(
    r"(?:LEA TAMBIÉN|Lea también|Lea:|Lea aquí|Lea además|Le puede interesar|"
    r"Le recomendamos|También le puede interesar|Puede leer:|Siga leyendo|"
    r"Ver más:|Ver también:|Vea también|Vea aquí|Leer más:|Consulte aquí):?\s*",
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
    r"|Siga (?:nuestro|el) (?:nuevo )?canal de WhatsApp"
    r"|No se pierda todo nuestro contenido multimedia"
    r"|No olviden? suscribirse a nuestro canal de YouTube"
    r"|[^\n]*aporte en nuestra Vaki"
    r")[^\n]*\n?",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Promos de marca detectadas EMPÍRICAMENTE en el corpus (auditoría ago-2026:
# líneas presentes en ≥30% de los artículos de una fuente)
# ---------------------------------------------------------------------------

_BRAND_PROMO_PATTERN = re.compile(
    r"^\s*(?:"
    r"Siga a EL PAÍS en Google Discover[^\n]*"     # elpaiscali (44/44)
    r"|\*?\s*Pulzo\.com se escribe con Z[^\n]*"    # pulzo (13/14)
    r"|Escuchar? este artículo"                    # elespectador (31/35)
    r"|Audio generado con IA(?: de Google)?[^\n]*" # elespectador (31/35)
    r"|Env[íi]e su antieditorial[^\n]*"            # elespectador (opinión)
    r"|[^\n]{0,40}usa cookies necesarias[^\n]*"    # cookie banner variante
    r")\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Encabezados de secciones/promos que aparecen como línea suelta en CUALQUIER
# parte del texto (el _SECTION_CUTOFF solo actúa en la 2ª mitad)
_SECTION_HEADER_LINES = re.compile(
    r"^\s*(?:"
    r"Temas recomendados:?|Noticias Destacadas|Lo más leído|Lo último|"
    r"Tendencias|En portada|Te puede interesar|Más sobre este tema|"
    r"Acerca del autor|\d+ comentarios|En vivo|Publicidad|"
    r"Reproducir|Escuchar"
    r")\s*$",
    re.IGNORECASE | re.MULTILINE,
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
    r"|^\s*Bloque de preguntas y respuestas\s*$"
    r")",
    re.MULTILINE,
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

# Cortes INEQUÍVOCOS: estas cadenas jamás son contenido editorial, así que se
# aplican SIN guarda de posición. La guarda del 50% existe para los cortes
# ambiguos ("Tendencias" puede ser prosa), pero bloqueaba el corte legítimo en
# notas cortas: un teaser de El Tiempo con 290 chars de cuerpo y 850 de promo
# arrancaba el bloque al 26% del texto y sobrevivía entero (ago-2026).
_BRAND_CUTOFF_PATTERN = re.compile(
    r"\n\s*"
    r"(?:BOLETINES EL TIEMPO|EL TIEMPO GOOGLE NEWS|EL TIEMPO WHATSAPP|"
    r"EL TIEMPO APP|SUSCRÍBETE AL DIGITAL|"
    r"Sigue toda la información de [A-Z][^\n]{1,60} en Facebook|"
    r"Conforme a los criterios de|"
    # Carrusel de relacionados al pie (contextoganadero: 12 líneas de firmas
    # de OTRAS notas tras este encabezado).
    r"Noticias?\s+Relacionadas?\s*$|Art[íi]culos?\s+Relacionados?\s*$|"
    r"Te\s+puede\s+interesar\s*$|Lo\s+m[áa]s\s+visto\s*$|"
    # Bloque de suscripción/comentarios al cierre (ambitojuridico: 3 de 4
    # artículos lo arrastraban).
    r"¡?Bienvenido a nuestra secci[óo]n de comentarios|"
    r"Gracias por leernos\b|"
    r"Para unirte a la conversaci[óo]n, necesitas estar suscrito)"
    r".*$",
    re.DOTALL | re.IGNORECASE,
)

# Cortes AMBIGUOS: pueden aparecer en prosa legítima, así que solo se aplican
# si el match cae en la mitad final del texto (ver _safe_section_cutoff).
_SECTION_CUTOFF_PATTERN = re.compile(
    r"\n\s*"
    r"(?:Más para ver|Nuestro mundo|Más noticias|Lo más leído|Lo último|"
    r"Más sobre|También en [A-Z]|Tendencias|En portada|Otras noticias|"
    r"Horóscopo\s*\n|Crucigrama\s*\n)"
    r".*$",
    re.DOTALL,
)

# Firma concatenada por trafilatura: "PERIODISTAActualizado:" — rol en
# mayúsculas pegado a la fecha de actualización.
_BYLINE_ARTIFACT = re.compile(
    r"^\s*[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s]{3,30}(?:Actualizado|Publicado)\s*:[^\n]*$",
    re.MULTILINE,
)

# Firma "Por<Autor> - <fecha>" pegada sin espacio, típica de los listados de
# notas relacionadas: "PorPedro Fonseca-16 de Abril 2026", "Por - 04 de
# Marzo 2014" (autor vacío). Nunca es prosa del artículo.
_MESES = (
    "enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|"
    "octubre|noviembre|diciembre"
)
_BYLINE_PATTERN = (
    r"Por\s*[^\n\d]{0,45}?-\s*\d{1,2}\s+de\s+(?:" + _MESES + r")"
    r"(?:\s+de)?\s+\d{4}"
)
_BYLINE_DATE_LINE = re.compile(
    r"^\s*" + _BYLINE_PATTERN + r"\s*$",
    re.MULTILINE | re.IGNORECASE,
)

# La firma del artículo va JUSTO después de su titular, así que todo lo que
# la precede es prefijo: el titular propio (ya está en el campo `title`) o
# basura de un carrusel de destacados. Caso real de contextoganadero: el
# texto abría con el titular y la firma de OTRA nota, luego "Cargando...",
# y solo entonces la firma real. Acotado a los primeros 400 chars, y greedy
# para cortar en la ÚLTIMA firma dentro de esa ventana.
_BYLINE_START_CUTOFF = re.compile(
    r"\A[\s\S]{0,400}^\s*" + _BYLINE_PATTERN + r"\s*$\n?",
    re.MULTILINE | re.IGNORECASE,
)

# Placeholders de carga que trafilatura recoge como texto.
_LOADING_PLACEHOLDER = re.compile(
    r"^\s*(?:Cargando\.{0,3}|Loading\.{0,3}|Publicidad|Advertisement)\s*$",
    re.MULTILINE | re.IGNORECASE,
)

# Marcador de inicio de artículo (El Tiempo prefija con "Noticia\n").
# El prefijo a borrar se ACOTA a los primeros 300 caracteres: sin la guarda,
# una línea suelta "Opinión"/"Entrevista" a mitad del cuerpo (módulo de nota
# relacionada) borraba todo lo anterior — artículos reducidos a la mitad en
# silencio (reproducido en la revisión ago-2026). Análogo a la guarda de
# posición de _safe_section_cutoff.
_ARTICLE_START_MARKER = re.compile(
    # El prefijo es OPCIONAL: cuando "Noticia" es la PRIMERA línea no hay
    # ningún \n antes y el marcador nunca disparaba (verificado en El Tiempo,
    # ago-2026).
    r"\A(?:[\s\S]{0,300}?\n)?\s*"
    r"(?:Noticia|Análisis|Opinión|Editorial|Reportaje|Crónica|Entrevista|"
    r"Exclusivo suscriptores)\s*\n",
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
    r"Exclusivo\s+suscriptores|"
    r"\d+\s*min(?:utos)?\s+de\s+lectura|"
    r"Foto[\s:]+[^\n]{0,80}|"           # "Foto: AFP" — pie corto, sin pie largo de fotonota
    r"Cr[eé]ditos?[\s:]+[^\n]{0,80}|"
    r"AFP|EFE|Reuters|Colprensa"        # créditos de agencia sueltos
    # `:?` final: "Compartir:" (con dos puntos) sobrevivía al ancla `$`
    # — 14/15 artículos de razonpublica lo arrastraban.
    r")\s*:?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Reproductor de audio embebido ("Escuchar este artículo"): quedaban el
# cronómetro y el separador como líneas sueltas — 31/35 de elespectador.
_MEDIA_PLAYER_UI = re.compile(
    r"^\s*(?:\d{1,2}:\d{2}(?::\d{2})?|/|\|)\s*$",
    re.MULTILINE,
)

# Etiqueta de sección suelta en una línea ("Política", "Economía", "Cali").
# Debe ser la línea COMPLETA y sin puntuación de oración: la prosa real no
# produce líneas de una sola palabra-sección.
_SECTION_LABEL_LINE = re.compile(
    r"^\s*(?:"
    r"Política|Politica|Economía|Economia|Judicial|Justicia|Nación|Nacion|"
    r"Internacional|Mundo|Opinión|Opinion|Columnistas|Editorial|Deportes|"
    r"Cultura|Entretenimiento|Tecnología|Tecnologia|Salud|Educación|"
    r"Educacion|Ambiente|Región|Region|Regiones|Bogotá|Bogota|Medellín|"
    r"Medellin|Cali|Barranquilla|Cartagena|Colombia|Actualidad|Últimas|"
    r"Ultimas|Destacados|Titulares"
    r")\s*$",
    re.MULTILINE,
)

# Separadores tipográficos sueltos (solo asteriscos/guiones/puntos). NO toca
# "* El nombre fue cambiado…" ni "*** Con el apoyo de…", que son notas
# editoriales legítimas verificadas en el corpus.
_SEPARATOR_LINE = re.compile(
    r"^\s*(?:[*\-–—_·•]{2,}|\*)\s*$",
    re.MULTILINE,
)

# Retorno de nota al pie ("↩︎", "↩︎ -"): artefacto de footnotes.
_FOOTNOTE_BACKREF = re.compile(r"^\s*[↩⏎][\ufe0e\ufe0f]?\s*[-–—]?\s*$", re.MULTILINE)

# CTAs modernos que abren con emoji. Requieren emoji inicial Y verbo de CTA:
# así no se toca una cita de tuit con emoji que sí sea contenido.
_EMOJI_CTA_LINE = re.compile(
    r"^\s*[\U0001F300-\U0001FAFF\u2190-\u21FF\u2600-\u27BF\uFE0F\u200d]+"
    r"[^\n]*?\b(?:"
    r"lea|leer|le[ée]|invitamos|suscr[íi]b|s[íi]ga(?:nos)?|siga|ent[eé]rese|"
    r"se enter[óo]|inter[ée]s en m[áa]s|escuche|escuchar|vea|mire|descargue|"
    r"[úu]nase|[úu]nete|canal|whatsapp|newsletter|bolet[íi]n|clic"
    r")\b[^\n]*\n?",
    re.IGNORECASE | re.MULTILINE,
)

# Nombre de una entidad pública como línea suelta (firma de comunicado).
# Crítico ahora que 139 de las 434 fuentes son institucionales: el footer
# dinámico por `source_name` no lo atrapa (usa la KEY, "mininterior", no el
# nombre visible "Ministerio del Interior" — 10/10 artículos lo arrastraban).
_INSTITUTION_LINE = re.compile(
    r"^\s*(?:Ministerio|Minist(?:ra|ro)|Alcald[íi]a|Gobernaci[óo]n|Concejo|"
    r"Asamblea|Contralor[íi]a|Procuradur[íi]a|Defensor[íi]a|Personer[íi]a|"
    r"Superintendencia|Agencia|Unidad|Instituto|Departamento\s+Nacional|"
    r"Fiscal[íi]a|Registradur[íi]a|Presidencia|Vicepresidencia|Consejo|"
    r"Corte|Tribunal|Comisi[óo]n|Federaci[óo]n|Cámara|Camara|Senado)"
    r"[^\n]{0,60}$",
    re.MULTILINE,
)

# ---------------------------------------------------------------------------
# URLs residuales (trafilatura normalmente las quita, pero a veces quedan).
# Incluye embeds SIN esquema (pic.twitter.com/xyz, t.co/abc) que la versión
# anterior dejaba pasar — detectados en la auditoría del corpus.
# ---------------------------------------------------------------------------
_URL_PATTERN = re.compile(
    r"https?://\S+|www\.\S+"
    r"|\b(?:pic\.twitter\.com|t\.co|bit\.ly|youtu\.be|goo\.gl|tinyurl\.com)/\S+"
)

# Emails en CUALQUIER posición (antes solo se removían al final del texto).
# Un correo no aporta señal ideológica y sí arrastra CTAs de contacto.
_EMAIL_ANYWHERE_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")

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


def _dedupe_consecutive_lines(text: str) -> str:
    """Colapsa líneas consecutivas idénticas.

    Varios CMS emiten el lede dos veces (resumen + primer párrafo) y
    trafilatura lo captura duplicado, inflando el texto que ve el modelo.
    Solo consecutivas: una frase repetida a distancia puede ser legítima.
    """
    out: list[str] = []
    previous = None
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped and stripped == previous:
            continue
        out.append(line)
        if stripped:
            previous = stripped
    return "\n".join(out)


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
    1. Cortes inequívocos de marca (promos de El Tiempo) y firma concatenada
    2. Cookie banners (completo + restos + restos de UI legal)
    3. Paywalls y UI de chatbot
    4. Bloques CTA + frases inline + redes sociales
    5. URLs residuales
    6. UI shorts (Compartir, Foto:, etc.) + reproductor, etiquetas de
       sección, separadores, notas al pie y firmas institucionales
    7. Firmas, emails, handles y footers al final
    8. Footer dinámico de la fuente (si source_name)
    9. Section cutoff seguro (solo si el match está en la mitad final)
    10. Líneas con solo el nombre del autor
    11. Normalización de espacios
    """
    # 0. Normalización Unicode: \xa0 → " ", caracteres compatibility, etc.
    text = unicodedata.normalize("NFKC", text)

    # 1. Cortes inequívocos de marca/relacionados (sin guarda) y firmas
    text = _BRAND_CUTOFF_PATTERN.sub("", text)
    text = _BYLINE_ARTIFACT.sub("", text)
    text = _BYLINE_START_CUTOFF.sub("", text, count=1)
    text = _BYLINE_DATE_LINE.sub("", text)
    text = _LOADING_PLACEHOLDER.sub("", text)

    # 2. Cookie banners
    text = _COOKIE_BANNER_PATTERN.sub("", text)
    text = _COOKIE_BANNER_REMNANT_PATTERN.sub("\n", text)
    text = _UI_REMNANT_PATTERN.sub("", text)

    # 3. Paywall y UI de chatbot
    text = _PAYWALL_PATTERN.sub("", text)
    text = _UI_NOISE_PATTERN.sub("", text)

    # 4. CTAs (bloques + inline + redes + promos de marca + encabezados)
    text = _CTA_BLOCK_PATTERN.sub("", text)
    text = _CTA_INLINE_PATTERN.sub("", text)
    text = _SOCIAL_CTA_PATTERN.sub("", text)
    text = _BRAND_PROMO_PATTERN.sub("", text)
    text = _SECTION_HEADER_LINES.sub("", text)
    text = _EMOJI_CTA_LINE.sub("", text)

    # Marcador de inicio DESPUÉS de limpiar el ruido: si corre antes, un
    # prefijo largo de CTAs agota la guarda de 300 chars y no dispara.
    text = _ARTICLE_START_MARKER.sub("", text, count=1)

    # 5. URLs y emails residuales
    text = _URL_PATTERN.sub("", text)
    text = _EMAIL_ANYWHERE_PATTERN.sub("", text)

    # 6. UI shorts (Compartir, Foto: pie corto, créditos de agencia) y
    #    artefactos de plantilla: reproductor, etiquetas de sección,
    #    separadores, notas al pie y firmas institucionales.
    text = _UI_SHORT_LINES.sub("", text)
    text = _MEDIA_PLAYER_UI.sub("", text)
    text = _SECTION_LABEL_LINE.sub("", text)
    text = _SEPARATOR_LINE.sub("", text)
    text = _FOOTNOTE_BACKREF.sub("", text)
    text = _INSTITUTION_LINE.sub("", text)

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

    # 11. Colapsar líneas consecutivas repetidas (lede duplicado por el CMS)
    text = _dedupe_consecutive_lines(text)

    # 12. Normalizar espacios
    text = _TRAILING_SPACES.sub("", text)
    text = _MULTI_NEWLINES.sub("\n\n", text)
    text = text.strip()

    return text
