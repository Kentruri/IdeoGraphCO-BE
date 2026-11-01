"""Verificación de robots.txt para cumplimiento ético del scraping.

Corregido respecto a la versión de la tesis:
- El cache se keyea por DOMINIO (antes recibía la URL completa del artículo,
  así que nunca acertaba y re-descargaba robots.txt por cada candidata).
- La descarga tiene timeout (RobotFileParser.read() usa urllib sin timeout;
  un dominio colgado bloqueaba el cron indefinidamente).
"""

import logging
import urllib.request
from functools import lru_cache
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

logger = logging.getLogger(__name__)

_BOT_NAME = "*"
_ROBOTS_TIMEOUT_SECONDS = 10


@lru_cache(maxsize=256)
def _get_robot_parser(domain_base: str) -> RobotFileParser | None:
    """Descarga y parsea el robots.txt de un dominio (cacheado por dominio).

    `domain_base` debe ser "scheme://netloc" (lo garantiza is_url_allowed).
    """
    robots_url = f"{domain_base}/robots.txt"
    rp = RobotFileParser()
    rp.set_url(robots_url)
    try:
        with urllib.request.urlopen(robots_url, timeout=_ROBOTS_TIMEOUT_SECONDS) as response:
            content = response.read().decode("utf-8", errors="ignore")
        rp.parse(content.splitlines())
        return rp
    except Exception:
        logger.warning("No se pudo leer robots.txt de %s", robots_url)
        # Si no se puede leer, se permite el scraping por defecto
        return None


def is_url_allowed(url: str) -> bool:
    """Verifica si una URL está permitida según el robots.txt del dominio."""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return False
    parser = _get_robot_parser(f"{parsed.scheme}://{parsed.netloc}")
    if parser is None:
        return True
    return parser.can_fetch(_BOT_NAME, url)
