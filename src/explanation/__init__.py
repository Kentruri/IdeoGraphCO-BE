"""Explicación de la clasificación en lenguaje natural.

Para cada artículo clasificado, un LLM redacta un párrafo de 3 o 4 frases que
dice qué elementos del texto corresponden a su clase predicha y, si la segunda
clase pesa (≥ 20 % por defecto), también a esa. Lo escribe a partir de tres
cosas: el titular y el texto que leyó el clasificador, la distribución que
devolvió y el codebook de las ocho clases.

No es el razonamiento del clasificador: BETO no se explica a sí mismo y el LLM
no sabe qué miró. Por eso la API la entrega con un rótulo que lo dice y el
prompt le prohíbe atribuirle intenciones al modelo.

- `codebook.py`: copia de las definiciones del codebook del repo de ML.
- `prompts.py`: system prompt (codebook + reglas de neutralidad) y prompt por artículo.
- `generator.py`: llamada al LLM (`claude -p`) y validación de la respuesta.
- `basis.py`: qué clasificación explica cada texto guardado y si sigue vigente.
- `pipeline.py`: genera y guarda las que faltan (ingesta y `scripts/explicar.py`).
"""
