FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8094 DATABASE_PATH=/data/devisia.sqlite
WORKDIR /app
COPY requirements.txt pyproject.toml MANIFEST.in ./
COPY app ./app
COPY wsgi.py asgi.py run.py ./
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .
RUN mkdir -p /data && chown -R 10001:10001 /app /data
USER 10001
EXPOSE 8094
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8094/api/health', timeout=3)"
CMD ["gunicorn", "--bind", "0.0.0.0:8094", "--workers", "1", "--threads", "4", "--timeout", "120", "wsgi:application"]
