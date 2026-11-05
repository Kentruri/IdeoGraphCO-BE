# Contrato BE ↔ FE — v2 (2026-07-29; `/classify` con filtro de dominio y filtros del listado, 2026-11-04)

Contrato de datos entre **IdeoGraphCO-BE** (este repo) e **IdeoGraphCO-FE**.
Todos los campos van en `snake_case`.

**Fechas** (`published_at`, `generated_at`): ISO 8601 **siempre en UTC con
zona**, sufijo `Z` (`"2026-07-30T12:21:00Z"`), con SQLite y con Postgres. La
ingesta guarda en UTC (las fechas del RSS vienen en UTC; una fecha de los
metadatos con otro desfase se pasa a UTC; una sin zona se toma como UTC).
El FE decide la zona en que la muestra.

**Medios**: `source_name` es el slug del catálogo (`lasillavacia`), estable
para filtrar o enlazar; `source_display_name` es el nombre para mostrar
(`La Silla Vacía`). Si un slug ya no está en el catálogo, ambos traen el slug.

## Conceptos

- **Article**: un artículo real publicado por un medio colombiano. Cada uno
  tiene su **propia clasificación ideológica** (se ejecuta al guardarse).
- **Story**: la unidad que se lista y se navega. Agrupa 1..5 articles que
  cubren la misma historia. Puede tener una **noticia sintetizada** (nullable;
  solo cuando hay ≥3 fuentes — Fase 3).
- El público **no ve el texto completo** de los articles originales: ve
  título + snippet + clasificación + link al medio. El único cuerpo completo
  que se publica es el de la sintética (contenido generado propio, etiquetado).

## Las 8 clases (compartidas con IdeoGraphCO y el FE)

`personalismo, institucionalismo, populismo, doctrinarismo, soberanismo,
globalismo, conservadurismo, progresismo`

```jsonc
// ClassificationDTO — presente en cada article clasificado
{
  "label": "populismo",                  // argmax
  "probabilities": { "personalismo": 0.12, /* …8 claves, suman 1.0 */ },
  "model_version": "mock-0.1.0"          // o la versión real del clasificador
}
```

## Endpoints

### `GET /api/v1/stories?page=1&page_size=9&source_category=nacional&ideology=populismo&published_from=2026-07-01&q=reforma`

Listado paginado. Solo stories con `status = "published"`. Orden:
`published_at DESC, id DESC`. Todos los filtros son opcionales y se combinan
con Y; `total_items` y `total_pages` cuentan lo que queda después de filtrar.

| Parámetro | Valor | Filtra |
|---|---|---|
| `page` | ≥ 1 (por defecto 1; si pasa de `total_pages`, se responde la última) | — |
| `page_size` | 1 a 50 (por defecto 9) | — |
| `source_category` | una de las 7 categorías | categoría del artículo representativo |
| `ideology` | una o varias de las 8 clases, repitiendo el parámetro (`?ideology=populismo&ideology=progresismo`) o separadas por comas (`?ideology=populismo,progresismo`) | clase predicha (`classification.label`) del representativo; basta con que coincida una |
| `published_from` | fecha `YYYY-MM-DD` o fecha y hora ISO 8601 | `published_at` ≥ ese límite |
| `published_to` | fecha `YYYY-MM-DD` o fecha y hora ISO 8601 | `published_at` ≤ ese límite |
| `q` | texto, máx. 200 caracteres | título, subtítulo o medio de cualquier artículo de la story (sin tildes ni mayúsculas) |

- Un parámetro vacío (`?source_category=`, `?ideology=`, `?published_from=`)
  se ignora. Una categoría, una clase o una fecha que no se reconoce
  responde `400` con `{ "detail": "…" }`.
- **Fechas.** Con solo la fecha (`2026-07-01`), el límite es ese día
  calendario **en UTC**, completo e inclusivo: `published_from` toma sus
  00:00 y `published_to` llega hasta sus 23:59:59.999999. Con fecha y hora
  (`2026-07-01T05:00:00Z`, `2026-07-01T00:00:00-05:00`) se usa ese instante
  exacto, también inclusivo; sin zona se asume UTC. En la URL, el `+` de una
  zona se escribe `%2B`. Para filtrar por días de otra zona (p. ej. la del
  navegador), el cliente envía los instantes de su medianoche: el FE manda
  `published_from=<00:00 local en UTC>` y `published_to=<23:59:59.999 local en UTC>`.
  Un rango invertido no es error: devuelve 0 resultados.

```jsonc
{
  "items": [
    {
      "id": "st_9f2c…",
      "slug": "reforma-pensional-conciliacion-3fa9b2",
      "title": "…",                       // título del artículo representativo
      "subtitle": "…" ,                   // nullable
      "source_name": "eltiempo",          // slug del catálogo
      "source_display_name": "El Tiempo", // nombre para mostrar
      "source_category": "nacional",      // nacional|independiente|regional|institucional|judicial|opinion|gremial
      "author": "…",                      // nullable
      "published_at": "2026-07-29T13:30:00Z",  // UTC
      "image_url": "https://…",           // nullable
      "reading_time_minutes": 4,
      "classification": { /* ClassificationDTO */ },  // del representativo
      "sources_count": 1,                 // # de fuentes en la story
      "has_synthesized": false
    }
  ],
  "page": 1, "page_size": 9, "total_items": 42, "total_pages": 5
}
```

**Artículo representativo**: el article con `published_at` más antiguo de la
story (el primero en reportar). Determina título/medio/clasificación del listado.

### `GET /api/v1/stories/{slug}`

Detalle completo. Los errores usan el formato estándar de FastAPI:
`404`/`400` responden `{ "detail": "…" }`.

```jsonc
{
  // …mismos campos del item de listado, más:
  "keywords": ["reforma pensional", "congreso"],
  "synthesized": {                        // null si no hay síntesis
    "title": "…",
    "body": ["párrafo 1", "párrafo 2"],
    "model": "gemini-2.5-flash",
    "generated_at": "2026-07-29T18:00:00Z",
    "disclaimer": "Resumen generado automáticamente por IA a partir de 4 fuentes; puede contener errores."
  },
  "sources": [                            // SIEMPRE ≥1; cada una con su clasificación
    {
      "id": "a1b2c3d4e5f6a7b8",
      "source_name": "eltiempo",
      "source_display_name": "El Tiempo",
      "source_category": "nacional",
      "title": "…",
      "snippet": "Primeras ~300 caracteres del artículo…",  // NUNCA el body completo
      "url": "https://www.eltiempo.com/…",   // link al artículo original
      "author": "…",                      // nullable
      "published_at": "2026-07-29T15:05:00Z",
      "classification": { /* ClassificationDTO */ }          // nullable si pending
    }
  ]
}
```

### `GET /api/v1/health`

`{ "status": "ok", "version": "0.1.0" }`

### `POST /api/v1/classify`

Mesa de redacción: clasifica un texto pegado por el usuario **sin
guardarlo**. Usa el mismo clasificador que la ingesta (`CLASSIFIER_MODE`).

**Filtro de dominio opcional.** El modelo se entrenó solo con noticias
políticas colombianas. Si el BE tiene `GEMINI_API_KEY`, antes de clasificar
el texto pasa por el **mismo filtro de politicidad de la ingesta**
(`src/ingestion/political_filter.py`: Gemini 2.5 Flash-Lite, que escala a
Flash si su confianza es < 0,7; solo el cuerpo, recortado a 3.000
caracteres). Tres casos:

| Caso | Qué hace | `political_filter` |
|---|---|---|
| Sin `GEMINI_API_KEY` (lo normal hoy) | No llama a ningún LLM: se confía en el usuario y se clasifica todo texto de 40 a 30.000 caracteres. Un aviso al arrancar en el log. | `"disabled"` |
| Con clave, el filtro acepta el texto | Clasifica. | `"passed"` |
| Con clave, el filtro no lo acepta | **No** clasifica: 422 `not_political`. | — |
| Con clave, el filtro falla (cuota, caído, respuesta ilegible) | Clasifica igual, sin bloquear (un solo intento, sin esperas); aviso en el log. | `"unavailable"` |

```jsonc
// request
{ "title": "…",   // opcional (máx. 300); el modelo se entrenó con titular + cuerpo
  "text": "…" }   // 40 a 30.000 caracteres

// 200 → ClassificationDTO + political_filter
{ "label": "institucionalismo", "probabilities": { /* 8 clases */ },
  "model_version": "beto-cribado-s42",
  "political_filter": "disabled" }   // "disabled" | "passed" | "unavailable" (ver tabla)

// 422 not_political → solo con clave: el filtro dice que no es una noticia política colombiana
{ "detail": "El texto no parece una noticia política colombiana; …",
  "code": "not_political",
  "category": "nonpolitical_article",  // nonpolitical_article | biography_static | garbage
  "confidence": 0.97,                  // nullable
  "reason": "Receta de cocina" }       // ≤ 12 palabras, la escribe el filtro

// 422 de validación (texto fuera de rango) → formato estándar de FastAPI,
//     { "detail": [ … ] } sin `code`
// 503 → clasificador no disponible ({ "detail": "…" })
```

El FE distingue los dos 422 por `code === "not_political"`.

## Reglas de renderizado para el FE

1. El cuerpo principal del detalle es `synthesized.body` si existe; si es
   `null` (story de fuente única o síntesis pendiente), el FE muestra el
   snippet de la fuente + botón "Leer en {source_display_name}" hacia `url`.
   En la UI el medio siempre se nombra con `source_display_name`.
2. `synthesized` siempre se muestra con su `disclaimer` visible.
3. `classification` puede ser `null` en `sources[]` (clasificación pendiente);
   en el item de listado nunca es `null` (una story no se publica sin
   clasificar su representativo).
4. Campos nullable: `subtitle`, `author`, `image_url`, `synthesized`,
   `sources[].classification`.

## Diferencias vs. el mock actual del FE (migración)

| Mock FE hoy | Contrato v2 |
|---|---|
| `GET /api/articles` | `GET /api/v1/stories` |
| `GET /api/articles/{slug}` | `GET /api/v1/stories/{slug}` |
| detalle con `body: string[]` del artículo | detalle con `synthesized` nullable + `sources[]` con snippet |
| `classification` obligatoria en detalle | obligatoria en listado; nullable por fuente |
| — | nuevos: `sources_count`, `has_synthesized`, `sources[]`, `synthesized` |
