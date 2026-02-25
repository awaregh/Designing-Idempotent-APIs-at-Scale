FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt
COPY services/shared /app/services/shared
ARG SERVICE_NAME
COPY services/api_variants/${SERVICE_NAME} /app/services/api_variants/${SERVICE_NAME}
# Create __init__.py files for proper package resolution
RUN touch /app/__init__.py /app/services/__init__.py /app/services/api_variants/__init__.py
ENV PYTHONPATH=/app
CMD ["sh", "-c", "python -m uvicorn services.api_variants.${SERVICE_NAME}.main:app --host 0.0.0.0 --port 8000"]
