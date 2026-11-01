# IdeoGraphCO-BE

Backend del prototipo de la tesis **IdeoGraphCO**: ingesta recurrente de
noticias políticas colombianas, clasificación ideológica al guardar (vía el
servicio del repo IdeoGraphCO) y API REST de lectura para IdeoGraphCO-FE.

Contrato de datos con el frontend: [CONTRACT.md](CONTRACT.md).

## Arquitectura (100% free tier)

```
GitHub Actions (cron cada 6h + botón manual "Run workflow")
        │  scripts/ingest.py
        ▼
Ingesta: RSS/sitemaps de medios CO → trafilatura → limpieza → filtro
politicidad (Gemini) → clasificador (servicio IdeoGraphCO) → Postgres
        │
        ▼
Supabase Postgres (stories + articles)
        ▲
        │  solo lectura
FastAPI (Hugging Face Spaces) ──► IdeoGraphCO-FE (Next.js)
```

- **stories**: la unidad navegable (agrupa 1..5 artículos de la misma
  historia; en Fase 3 guarda la noticia sintetizada por LLM).
- **articles**: cada artículo real de un medio, con su **propia**
  clasificación (label + distribución softmax de 8 clases + model_version).
- El texto completo de los artículos es **solo interno** (clasificación /
  síntesis); la API pública expone título + snippet + link al medio.

## Correr local

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # defaults: SQLite + clasificador mock

# Ingesta de prueba (2 artículos de una fuente, sin filtro LLM)
python scripts/ingest.py --sources lasillavacia --max-per-source 2 --no-filter

# API
uvicorn src.api.app:app --reload --port 8000
# → http://localhost:8000/api/v1/stories  ·  /docs para el swagger

# Tests y lint
pytest && ruff check src tests scripts
```

## Pipeline de ingesta

`scripts/ingest.py` (el mismo que corre el cron):

1. **Descubrimiento** por fuente (catálogo de 83 medios en 7 categorías,
   portado del repo IdeoGraphCO): RSS primero (newest-first), sitemap como
   respaldo. Respeta `robots.txt` y rota User-Agent con delays adaptativos.
2. **Dedup idempotente**: por URL (`articles.url` UNIQUE) y por hash de
   contenido (cables de agencia republicados). Re-correr el cron no duplica.
3. **Extracción** con trafilatura + limpieza regex (CTAs, paywalls, firmas).
4. **Filtro de politicidad** (Gemini free tier, con escalado automático
   flash-lite → flash). Sin `GEMINI_API_KEY` se salta.
5. **Clasificación ideológica** llamando al servicio externo
   (`CLASSIFIER_MODE=http`) o al mock determinista (`mock`, default). Si el
   servicio falla, el artículo queda `pending` y se reintenta en la
   siguiente corrida; la story no se publica hasta clasificar.
6. **Persistencia**: hoy (Fase 1) cada artículo crea su story de fuente
   única. La Fase 2 agrega el clustering multi-fuente y la Fase 3 la
   síntesis LLM.

## Endpoints

| Ruta | Descripción |
|---|---|
| `GET /api/v1/stories?page&page_size&source_category&q` | Listado paginado (solo publicadas) |
| `GET /api/v1/stories/{slug}` | Detalle: representativo + `synthesized` (nullable) + `sources[]` |
| `GET /api/v1/health` | Health check |

## Despliegue gratuito

1. **Supabase**: crear proyecto → copiar la URL del *Session Pooler* como
   `postgresql+psycopg://…` → secret `DATABASE_URL` del repo.
2. **GitHub Actions**: el workflow [ingest.yml](.github/workflows/ingest.yml)
   ya trae el cron cada 6h y el trigger manual (pestaña Actions → *Ingesta
   de noticias* → *Run workflow*). Secrets: `DATABASE_URL`, `GEMINI_API_KEY`.
3. **API en Hugging Face Spaces** (Docker Space con
   `uvicorn src.api.app:app --host 0.0.0.0 --port 7860`), env
   `DATABASE_URL` + `CORS_ORIGINS` con el dominio del FE.
4. Cuando exista el servicio de inferencia de IdeoGraphCO: variables
   `CLASSIFIER_MODE=http` y `CLASSIFIER_API_URL`.

## Roadmap

- **Fase 2 — Triangulación**: clustering de "misma historia" (embeddings de
  título+lead, ventana 24–48h, entidades compartidas), validado contra los
  events de Event Registry (free tokens) y una muestra manual. GDELT como
  ampliador de fuentes.
- **Fase 3 — Síntesis**: noticia neutral generada con Gemini a partir de
  ≥3 fuentes, una sola vez por story, siempre etiquetada como generada por
  IA (ver disclaimer en el contrato).
- **Manual**: endpoint autenticado para enviar texto/URL → clasificar →
  guardar (el flujo manual del prototipo).
