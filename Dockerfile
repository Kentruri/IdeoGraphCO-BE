# API de lectura para Hugging Face Spaces (Docker Space) o cualquier host.
# HF Spaces expone el puerto 7860 por convención.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/

# Usuario no-root (requerido por HF Spaces)
RUN useradd -m appuser
USER appuser

EXPOSE 7860
CMD ["uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "7860"]
