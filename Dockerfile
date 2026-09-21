FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# Run as a normal user, not root
RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY --chown=app:app *.py ./

# The cache and "not interested" list live in /app/data. Creating it here (owned by "app") means a
# fresh Docker volume mounted on it starts out writable by the app.
RUN mkdir data && chown app:app data
USER app

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"

CMD ["python", "web.py"]
