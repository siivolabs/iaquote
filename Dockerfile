FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN addgroup --system iaquote && adduser --system --ingroup iaquote iaquote \
    && mkdir -p /data && chown -R iaquote:iaquote /data /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=iaquote:iaquote . .
USER iaquote
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3)" || exit 1
CMD ["uvicorn", "run:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
