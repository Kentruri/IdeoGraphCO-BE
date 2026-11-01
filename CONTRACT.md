# Contrato BE ↔ FE — v2 (2026-07-29)

Contrato de datos entre **IdeoGraphCO-BE** (este repo) e **IdeoGraphCO-FE**.
Todos los campos van en `snake_case`. Fechas en ISO 8601 con zona horaria.

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

### `GET /api/v1/stories?page=1&page_size=9&source_category=nacional&q=reforma`

Listado paginado. Solo stories con `status = "published"`. Orden:
`published_at DESC, id DESC`. `source_category` filtra por la categoría del
artículo representativo; `q` busca en título/subtítulo/medio.

```jsonc
{
  "items": [
    {
      "id": "st_9f2c…",
      "slug": "reforma-pensional-conciliacion-3fa9b2",
      "title": "…",                       // título del artículo representativo
      "subtitle": "…" ,                   // nullable
      "source_name": "eltiempo",
      "source_category": "nacional",      // nacional|independiente|regional|institucional|judicial|opinion|gremial
      "author": "…",                      // nullable
      "published_at": "2026-07-29T13:30:00+00:00",
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
    "generated_at": "2026-07-29T18:00:00+00:00",
    "disclaimer": "Resumen generado automáticamente por IA a partir de 4 fuentes; puede contener errores."
  },
  "sources": [                            // SIEMPRE ≥1; cada una con su clasificación
    {
      "id": "a1b2c3d4e5f6a7b8",
      "source_name": "eltiempo",
      "source_category": "nacional",
      "title": "…",
      "snippet": "Primeras ~300 caracteres del artículo…",  // NUNCA el body completo
      "url": "https://www.eltiempo.com/…",   // link al artículo original
      "author": "…",                      // nullable
      "published_at": "…",
      "classification": { /* ClassificationDTO */ }          // nullable si pending
    }
  ]
}
```

### `GET /api/v1/health`

`{ "status": "ok", "version": "0.1.0" }`

## Reglas de renderizado para el FE

1. El cuerpo principal del detalle es `synthesized.body` si existe; si es
   `null` (story de fuente única o síntesis pendiente), el FE muestra el
   snippet de la fuente + botón "Leer en {source_name}" hacia `url`.
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
