"""Catálogo de fuentes de noticias colombianas para IdeoGraphCO.

Cada fuente define su estrategia de extracción:
- mode "sitemap": Descarga sitemap.xml y filtra URLs por sección política
- mode "direct": Todo el contenido es político, scrapear sin filtro

Flujo de prioridades por fuente:
1. Si tiene rss_feeds → RSS primero (más limpio y rápido)
2. Si mode="sitemap" → sitemap + url_filters
3. Si mode="direct" → sitemap sin filtro (para tener histórico)
4. Fallback → crawl de la página con trafilatura
"""

# Secciones de URL que indican contenido político/relevante
DEFAULT_POLITICAL_SECTIONS: list[str] = [
    "/politica/", "/nacion/", "/gobierno/", "/economia/",
    "/opinion/", "/judicial/", "/justicia/", "/congreso/",
    "/elecciones/", "/paz/", "/conflicto/", "/seguridad/",
    "/legislacion/", "/poder/", "/negocios/", "/empresas/",
]

# ---------------------------------------------------------------------------
# Catálogo de fuentes — formato por fuente
# ---------------------------------------------------------------------------

SOURCES: dict[str, dict] = {
    # ===========================
    # NACIONALES (línea base ideológica)
    # ===========================
    "eltiempo": {
        "url": "https://www.eltiempo.com",
        "category": "nacional",
        "mode": "sitemap",
        "url_filters": ["/politica/", "/economia/", "/nacion/", "/justicia/", "/gobierno/"],
        "rss_feeds": [
            "https://www.eltiempo.com/rss/politica.xml",
            "https://www.eltiempo.com/rss/colombia.xml",
        ],
    },
    "elespectador": {
        "url": "https://www.elespectador.com",
        "category": "nacional",
        "mode": "sitemap",
        "url_filters": ["/politica/", "/economia/", "/colombia/", "/judicial/", "/opinion/"],
        "rss_feeds": [],
    },
    "semana": {
        "url": "https://www.semana.com",
        "category": "nacional",
        "mode": "sitemap",
        "url_filters": ["/nacion/", "/politica/", "/economia/", "/opinion/"],
        "rss_feeds": [],
    },
    "elnuevosiglo": {
        "url": "https://www.elnuevosiglo.com.co",
        "category": "nacional",
        "mode": "sitemap",
        "url_filters": ["/politica/", "/economia/", "/opinion/", "/nacion/"],
        "rss_feeds": [],
    },
    "portafolio": {
        "url": "https://www.portafolio.co",
        "category": "nacional",
        "mode": "sitemap",
        "url_filters": ["/economia/", "/negocios/", "/empresas/", "/opinion/"],
        "rss_feeds": [],
    },
    "bluradio": {
        "url": "https://www.bluradio.com",
        "category": "nacional",
        "mode": "sitemap",
        "url_filters": ["/politica/", "/nacion/", "/economia/", "/judicial/"],
        "rss_feeds": [],
    },
    "rcnradio": {
        "url": "https://www.rcnradio.com",
        "category": "nacional",
        "mode": "sitemap",
        "url_filters": ["/politica/", "/nacion/", "/economia/", "/judicial/"],
        "rss_feeds": [],
    },
    "caracol": {
        "url": "https://www.caracol.com.co",
        "category": "nacional",
        "mode": "sitemap",
        "url_filters": ["/politica/", "/economia/", "/judicial/"],
        "rss_feeds": [],
    },

    # ===========================
    # INDEPENDIENTES (populismo, doctrinarismo, progresismo)
    # ===========================
    "lasillavacia": {
        "url": "https://www.lasillavacia.com",
        "category": "independiente",
        "mode": "direct",  # 100% político
        "url_filters": [],
        "rss_feeds": [
            "https://www.lasillavacia.com/feed/",
        ],
    },
    "cambio": {
        "url": "https://cambiocolombia.com",
        "category": "independiente",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },
    "cuestionpublica": {
        "url": "https://cuestionpublica.com",
        "category": "independiente",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },
    "voragine": {
        "url": "https://voragine.co",
        "category": "independiente",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [
            "https://voragine.co/feed/",
        ],
    },
    "lanuevaprensa": {
        "url": "https://www.lanuevaprensa.com.co",
        "category": "independiente",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },
    "mutante": {
        "url": "https://www.mutante.org",
        "category": "independiente",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [
            "https://www.mutante.org/feed/",
        ],
    },
    "razonpublica": {
        "url": "https://razonpublica.com",
        "category": "independiente",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [
            "https://razonpublica.com/feed/",
        ],
    },
    "volcanicas": {
        "url": "https://volcanicas.com",
        "category": "independiente",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [
            "https://volcanicas.com/feed/",
        ],
    },
    "manifiesta": {
        "url": "https://manifiesta.org",
        "category": "independiente",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },
    "las2orillas": {
        "url": "https://www.las2orillas.co",
        "category": "independiente",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [
            "https://www.las2orillas.co/feed/",
        ],
    },
    "pulzo": {
        "url": "https://www.pulzo.com",
        "category": "independiente",
        "mode": "sitemap",
        "url_filters": ["/politica/", "/nacion/", "/economia/"],
        "rss_feeds": [
            "https://www.pulzo.com/rss",
        ],
    },

    # ===========================
    # OPINIÓN / ANÁLISIS (refuerzan doctrinarismo y análisis político)
    # ===========================
    "dejusticia": {
        "url": "https://www.dejusticia.org",
        "category": "opinion",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [
            "https://www.dejusticia.org/feed/",
        ],
    },
    "pares": {
        "url": "https://www.pares.com.co",
        "category": "opinion",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [
            "https://www.pares.com.co/feed/",
        ],
    },
    "latinoamerica21": {
        "url": "https://latinoamerica21.com",
        "category": "opinion",
        "mode": "sitemap",
        "url_filters": ["/colombia/", "/politica/", "/opinion/"],
        "rss_feeds": [
            "https://latinoamerica21.com/feed/",
        ],
    },

    # ===========================
    # INSTITUCIONALES (institucionalismo puro)
    # ===========================
    "presidencia": {
        "url": "https://www.presidencia.gov.co",
        "category": "institucional",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },
    "vicepresidencia": {
        "url": "https://www.vicepresidencia.gov.co",
        "category": "institucional",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },
    "funcionpublica": {
        "url": "https://www.funcionpublica.gov.co",
        "category": "institucional",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },
    "senado": {
        "url": "https://www.senado.gov.co",
        "category": "institucional",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },
    "camara": {
        "url": "https://www.camara.gov.co",
        "category": "institucional",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [
            "https://www.camara.gov.co/feed",
        ],
    },
    "contraloria": {
        "url": "https://www.contraloria.gov.co",
        "category": "institucional",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },
    "mininterior": {
        "url": "https://www.mininterior.gov.co",
        "category": "institucional",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [
            "https://www.mininterior.gov.co/feed/",
        ],
    },
    "cancilleria": {
        "url": "https://www.cancilleria.gov.co",
        "category": "institucional",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },
    "corteconstitucional": {
        "url": "https://www.corteconstitucional.gov.co",
        "category": "judicial",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },

    # ===========================
    # REGIONALES (personalismo local, soberanismo)
    # ===========================
    "elcolombiano": {
        "url": "https://www.elcolombiano.com",
        "category": "regional",
        "mode": "sitemap",
        "url_filters": ["/colombia/", "/politica/", "/negocios/", "/opinion/"],
        "rss_feeds": [
            "https://www.elcolombiano.com/rss/colombia.xml",
        ],
    },
    "elheraldo": {
        "url": "https://www.elheraldo.co",
        "category": "regional",
        "mode": "sitemap",
        "url_filters": ["/politica/", "/economia/", "/judicial/", "/opinion/", "/colombia/"],
        "rss_feeds": [],
    },
    "eluniversal": {
        "url": "https://www.eluniversal.com.co",
        "category": "regional",
        "mode": "sitemap",
        "url_filters": ["/politica/", "/economia/", "/colombia/", "/opinion/"],
        "rss_feeds": [],
    },
    "vanguardia": {
        "url": "https://www.vanguardia.com",
        "category": "regional",
        "mode": "sitemap",
        "url_filters": ["/politica/", "/economia/", "/opinion/", "/area-metropolitana/"],
        "rss_feeds": [],
    },
    "laopinion": {
        "url": "https://www.laopinion.com.co",
        "category": "regional",
        "mode": "sitemap",
        "url_filters": ["/politica/", "/economia/", "/judicial/", "/opinion/"],
        "rss_feeds": [],
    },
    "elpaiscali": {
        "url": "https://www.elpais.com.co",
        "category": "regional",
        "mode": "sitemap",
        "url_filters": ["/politica/", "/economia/", "/judicial/", "/opinion/", "/cali/"],
        "rss_feeds": [],
    },

    # ===========================
    # GREMIALES (soberanismo vs. globalismo)
    # ===========================
    "fecode": {
        "url": "https://fecode.edu.co",
        "category": "gremial",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [
            "https://fecode.edu.co/feed/",
        ],
    },
    "andi": {
        "url": "https://www.andi.com.co",
        "category": "gremial",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },
    "valorescristianos": {
        "url": "http://periodicovalorescristianos.com",
        "category": "gremial",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },

    # =======================================================================
    # AMPLIACIÓN — fuentes adicionales para refuerzo de diversidad ideológica
    # =======================================================================

    # --- Nacionales y económicos ---
    "larepublica": {
        "url": "https://www.larepublica.co",
        "category": "nacional",
        "mode": "sitemap",
        "url_filters": [
            "/economia/", "/inside/", "/finanzas/",
            "/empresas/", "/analisis/", "/caja-fuerte/",
        ],
        "rss_feeds": [],  # usa sitemaps mensuales (gzip) como fuente prioritaria
    },
    "valoraanalitik": {
        "url": "https://www.valoraanalitik.com",
        "category": "nacional",
        "mode": "sitemap",
        "url_filters": [
            "/economia/", "/energia/", "/infraestructura/",
            "/mercados/", "/politica/",
        ],
        "rss_feeds": ["https://www.valoraanalitik.com/feed/"],
    },
    "dataifx": {
        "url": "https://www.dataifx.com",
        "category": "nacional",
        "mode": "sitemap",
        "url_filters": [
            "/noticias/", "/macroeconomia/", "/dolar/",
            "/mercados-y-finanzas/",
        ],
        "rss_feeds": ["https://www.dataifx.com/rss.xml"],
    },
    "infobae_colombia": {
        "url": "https://www.infobae.com/colombia/",
        "category": "nacional",
        "mode": "sitemap",
        "url_filters": [
            "/colombia/noticias/politica/", "/america/colombia/", "/economia/",
        ],
        "rss_feeds": [],
    },
    "kienyke": {
        "url": "https://www.kienyke.com",
        "category": "nacional",
        "mode": "sitemap",
        "url_filters": ["/politica/", "/kien-es-kien/", "/politica-y-poder/"],
        "rss_feeds": ["https://www.kienyke.com/feed"],
    },
    "publimetro_co": {
        "url": "https://www.publimetro.co",
        "category": "nacional",
        "mode": "sitemap",
        "url_filters": ["/noticias/", "/politica/", "/bogota/"],
        "rss_feeds": [],
    },

    # --- Independientes (territoriales, investigación, alternativos) ---
    "rutasdelconflicto": {
        "url": "https://rutasdelconflicto.com",
        "category": "independiente",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": ["https://rutasdelconflicto.com/feed/"],
    },
    "cerosetenta": {
        "url": "https://cerosetenta.uniandes.edu.co",
        "category": "independiente",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": ["https://cerosetenta.uniandes.edu.co/feed/"],
    },
    "ligacontraelsilencio": {
        "url": "https://ligacontraelsilencio.org",
        "category": "independiente",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": ["https://ligacontraelsilencio.org/feed/"],
    },
    "tercercanal": {
        "url": "https://tercercanal.co",
        "category": "independiente",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },
    "pluralidadz": {
        "url": "https://pluralidadz.com",
        "category": "independiente",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": ["https://pluralidadz.com/feed/"],
    },
    "prensarural": {
        "url": "https://prensarural.org",
        "category": "independiente",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": ["https://prensarural.org/spip/spip.php?page=backend"],
    },

    # --- Opinión / think tanks (políticas públicas, balance ideológico) ---
    "fedesarrollo": {
        "url": "https://www.fedesarrollo.org.co",
        "category": "opinion",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },
    "anif": {
        "url": "https://www.anif.co",
        "category": "opinion",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },
    "icpcolombia": {
        "url": "https://www.icpcolombia.org",
        "category": "opinion",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },
    "cinep": {
        "url": "https://www.cinep.org.co",
        "category": "opinion",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },
    "fescol": {
        "url": "https://colombia.fes.de",
        "category": "opinion",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },

    # --- Regionales (dinámicas locales y poderes territoriales) ---
    "proclamadelcauca": {
        "url": "https://www.proclamadelcauca.com",
        "category": "regional",
        "mode": "sitemap",
        "url_filters": ["/politica/", "/noticias-cauca/", "/opinion/"],
        "rss_feeds": ["https://www.proclamadelcauca.com/feed/"],
    },
    "diariodelsur": {
        "url": "https://diariodelsur.com.co",
        "category": "regional",
        "mode": "sitemap",
        "url_filters": ["/politica/", "/nacional/", "/opinion/"],
        "rss_feeds": [],
    },
    "elpilon": {
        "url": "https://elpilon.com.co",
        "category": "regional",
        "mode": "sitemap",
        "url_filters": ["/politica-pilon/", "/opinion-pilon/", "/economia-pilon/"],
        "rss_feeds": ["https://elpilon.com.co/feed/"],
    },
    "lapatria": {
        "url": "https://www.lapatria.com",
        "category": "regional",
        "mode": "sitemap",
        "url_filters": ["/politica/", "/economia/", "/nacional/", "/opinion/"],
        "rss_feeds": [],
    },
    "boyacasietedias": {
        "url": "https://boyacasietedias.com.co",
        "category": "regional",
        "mode": "sitemap",
        "url_filters": ["/noticias/", "/boyaca/", "/opinion/"],
        "rss_feeds": ["https://boyacasietedias.com.co/feed/"],
    },

    # --- Gremiales y sindicales (presión corporativa y sindicalismo) ---
    "asobancaria": {
        "url": "https://www.asobancaria.com",
        "category": "gremial",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },
    "sac": {
        "url": "https://sac.org.co",
        "category": "gremial",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },
    "fenalco": {
        "url": "https://www.fenalco.com.co",
        "category": "gremial",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },
    "colfecar": {
        "url": "https://colfecar.org.co",
        "category": "gremial",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },
    "cut": {
        "url": "https://cut.org.co",
        "category": "gremial",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": ["https://cut.org.co/feed/"],
    },

    # --- Institucionales (regulación del Estado, datos oficiales) ---
    "banrep": {
        "url": "https://www.banrep.gov.co",
        "category": "institucional",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },
    "dane": {
        "url": "https://www.dane.gov.co",
        "category": "institucional",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },
    "jep": {
        "url": "https://www.jep.gov.co",
        "category": "judicial",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },
    "fiscalia": {
        "url": "https://www.fiscalia.gov.co",
        "category": "judicial",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },
    "procuraduria": {
        "url": "https://www.procuraduria.gov.co",
        "category": "judicial",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },

    # =======================================================================
    # AMPLIACIÓN 2 — Internacional, judicial, regionales étnicos, think tanks
    # =======================================================================

    # --- Internacional con foco en Colombia ---
    "elpais_america": {
        "url": "https://elpais.com/america-colombia/",
        "category": "nacional",
        "mode": "sitemap",
        "url_filters": [
            "/america-colombia/", "/politica/", "/sociedad/", "/opinion/",
        ],
        "rss_feeds": ["https://elpais.com/rss/america/colombia.xml"],
    },

    # --- Judicial / técnico-legal (nueva categoría) ---
    "ambitojuridico": {
        "url": "https://www.ambitojuridico.com",
        "category": "judicial",
        "mode": "sitemap",
        "url_filters": [
            "/nacional/", "/administrativo/", "/constitucional/",
            "/penal/", "/laboral/",
        ],
        "rss_feeds": ["https://www.ambitojuridico.com/rss"],
    },
    "legis": {
        # OJO: paywall fuerte en buena parte del sitio; el filter LLM va a
        # descartar mucho como "garbage" si solo agarra previews.
        "url": "https://www.legis.com.co",
        "category": "judicial",
        "mode": "sitemap",
        "url_filters": ["/legislacion/", "/jurisprudencia/", "/doctrina/"],
        "rss_feeds": [],
    },

    # --- Regionales territoriales (zonas críticas no cubiertas antes) ---
    "choco7dias": {
        "url": "https://choco7dias.com",
        "category": "regional",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": ["https://choco7dias.com/feed/"],
    },
    "elmeridiano": {
        "url": "https://elmeridiano.co",
        "category": "regional",
        "mode": "sitemap",
        "url_filters": ["/politica/", "/judicial/", "/opinion/", "/cordoba/"],
        "rss_feeds": ["https://elmeridiano.co/feed/"],
    },
    "periodicodelmeta": {
        "url": "https://periodicodelmeta.com",
        "category": "regional",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": ["https://periodicodelmeta.com/feed/"],
    },
    "diariodelhuila": {
        "url": "https://www.diariodelhuila.com",
        "category": "regional",
        "mode": "sitemap",
        "url_filters": [
            "/politica/", "/economia/", "/judicial/", "/opinion/",
        ],
        "rss_feeds": ["https://www.diariodelhuila.com/feed/"],
    },

    # --- Voces étnicas y territoriales (representación) ---
    "agendapropia": {
        "url": "https://agendapropia.co",
        "category": "independiente",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": ["https://agendapropia.co/feed/"],
    },
    "viveafro": {
        "url": "https://revista-viveafro.com",
        "category": "independiente",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": ["https://revista-viveafro.com/feed/"],
    },

    # --- Think tanks adicionales (agenda política y electoral) ---
    "fip": {
        "url": "https://www.ideaspaz.org",
        "category": "opinion",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },
    "moe": {
        "url": "https://www.moe.org.co",
        "category": "opinion",
        "mode": "direct",
        "url_filters": [],
        "rss_feeds": [],
    },
}

# ---------------------------------------------------------------------------
# Vistas derivadas
# ---------------------------------------------------------------------------

CATEGORIES: list[str] = list({s["category"] for s in SOURCES.values()})

# Fuentes agrupadas por categoría
SOURCES_BY_CATEGORY: dict[str, list[str]] = {}
for _name, _conf in SOURCES.items():
    cat = _conf["category"]
    SOURCES_BY_CATEGORY.setdefault(cat, []).append(_name)
