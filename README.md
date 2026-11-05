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
politicidad (Gemini) → clasificador (servicio inference/) → Postgres
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
pytest && ruff check src tests scripts inference
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
| `GET /api/v1/stories?page&page_size&source_category&ideology&published_from&published_to&q` | Listado paginado (solo publicadas), con filtros opcionales (ver `CONTRACT.md`) |
| `GET /api/v1/stories/{slug}` | Detalle: representativo + `synthesized` (nullable) + `sources[]` |
| `GET /api/v1/health` | Health check |
| `POST /api/v1/classify` | Mesa de redacción: clasifica sin guardar. Con `GEMINI_API_KEY` pasa antes por el filtro de politicidad (422 `not_political` si no es del dominio); sin ella, confía en el usuario |

## Despliegue gratuito

1. **Supabase**: crear proyecto → copiar la URL del *Session Pooler* como
   `postgresql+psycopg://…` → secret `DATABASE_URL` del repo.
2. **GitHub Actions**: el workflow [ingest.yml](.github/workflows/ingest.yml)
   ya trae el cron cada 6h y el trigger manual (pestaña Actions → *Ingesta
   de noticias* → *Run workflow*). Secrets: `DATABASE_URL`, `GEMINI_API_KEY`.
3. **API en Hugging Face Spaces** (Docker Space con
   `uvicorn src.api.app:app --host 0.0.0.0 --port 7860`), env
   `DATABASE_URL` + `CORS_ORIGINS` con el dominio del FE. `GEMINI_API_KEY`
   es opcional: activa el filtro de politicidad de la mesa de redacción.
4. Servicio de inferencia (sección siguiente) en otro host con PyTorch;
   la ingesta lo usa con `CLASSIFIER_MODE=http` y `CLASSIFIER_API_URL`.

## Servicio de inferencia

`inference/` sirve el clasificador entrenado en el repo IdeoGraphCO. Va
aparte de la API de lectura porque necesita PyTorch (`inference/requirements.txt`,
con las mismas versiones del entrenamiento).

```bash
# 1. Exportar el checkpoint de Lightning a un artefacto autodescrito (~420 MB)
python -m inference.export_checkpoint \
    --checkpoint ../IdeoGraphCO/logs/checkpoints/cribado/beto__seed42/best.ckpt \
    --metrics ../IdeoGraphCO/logs/benchmark/cribado/beto__seed42/metrics.json \
    --model-version beto-cribado-s42 --out models/beto-cribado-s42

# 2. Levantar el servicio
IDEOGRAPH_ARTIFACT=models/beto-cribado-s42 uvicorn inference.api:app --port 8080

# 3. Ingestar clasificando con el modelo real
CLASSIFIER_MODE=http CLASSIFIER_API_URL=http://localhost:8080 \
    python scripts/ingest.py --sources lasillavacia --max-per-source 2 --no-filter
```

El artefacto guarda el troceo y el orden de clases del checkpoint, y el
predictor verifica el orden contra `src/core/schema.py` al cargar
(reglas 1 y 3 de `docs/traspaso-inferencia-BE.md` en IdeoGraphCO). La
entrada se compone con `inference/model_input.py`, copia literal de la del
entrenamiento (regla 2). `models/` no se versiona.

**Modelo provisional:** `beto-cribado-s42` es BETO del cribado (una
semilla, 5.000 artículos de entrenamiento, F1 Macro de validación 0,752).
Sirve para construir el prototipo; se reemplaza exportando el modelo
seleccionado del benchmark oficial con otra `--model-version`.

### Cambiar de modelo y reclasificar lo guardado

Cada artículo guarda la `model_version` que emitió su clasificación, y la
ingesta solo clasifica artículos nuevos o pendientes. Al cambiar de modelo,
lo ya guardado se pone al día con `scripts/reclasificar.py`:

```bash
# 1. Exportar el modelo elegido con una versión NUEVA (es lo que distingue
#    lo viejo de lo nuevo; nunca reutilizar una --model-version)
python -m inference.export_checkpoint \
    --checkpoint ../IdeoGraphCO/logs/checkpoints/<experimento>/<modelo>/best.ckpt \
    --metrics ../IdeoGraphCO/logs/benchmark/<experimento>/<modelo>/metrics.json \
    --model-version <modelo-final> --out models/<modelo-final>

# 2. Servirlo (reiniciar el servicio con el artefacto nuevo)
IDEOGRAPH_ARTIFACT=models/<modelo-final> uvicorn inference.api:app --port 8080

# 3. Contar cuánto hay que reclasificar (no llama al modelo ni cambia nada)
CLASSIFIER_MODE=http CLASSIFIER_API_URL=http://localhost:8080 \
    python scripts/reclasificar.py --dry-run

# 4. Reclasificar
CLASSIFIER_MODE=http CLASSIFIER_API_URL=http://localhost:8080 \
    python scripts/reclasificar.py
```

El comando le pregunta al servicio su versión (`GET /health`), toma los
artículos clasificados con cualquier otra y los vuelve a clasificar con el
mismo cliente de la ingesta, guardando clase, distribución y versión nueva.
Las stories publicadas muestran de inmediato la clasificación nueva de su
representativo. Al final informa cuántos cambiaron de clase y entre cuáles.

- **Por lotes y retomable**: guarda cada lote (`--batch-size`, 50 por
  defecto); si se corta, al volver a correrlo sigue con lo que falta.
  `--limit N` reparte el trabajo en varias corridas.
- **Fallos**: si el servicio no responde para un artículo, este conserva su
  clasificación anterior (la story sigue publicada) y queda para la próxima
  corrida; con 3 fallos seguidos se detiene. Si el servicio cambia de modelo
  a mitad de corrida, también se detiene sin mezclar versiones.
- **Pendientes**: los artículos sin clasificar no los toca; los reintenta la
  ingesta.
- Sale con código 1 si se detuvo o quedaron fallos, y 0 si terminó.

Con el mock (`CLASSIFIER_MODE=mock`, versión `mock-0.1.0`) se puede probar
sin el servicio: reclasifica todo lo que no esté en `mock-0.1.0`.

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
