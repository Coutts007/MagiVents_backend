# MagiVents API image. Production runs the API under Gunicorn (gunicorn_config.py) and the AI assistant from the
# same image under Gunicorn + Uvicorn workers (gunicorn_assistant.py); docker-compose.yml (development) runs runserver.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# No system packages needed: psycopg2-binary bundles libpq, and reportlab / Pillow ship wheels
WORKDIR /app

COPY requirements.txt /app/
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . /app/

# Static files (admin, API docs) are collected into the image and served by WhiteNoise.
# The secret key here is only for this build step; the real one comes from the environment at run time.
RUN DJANGO_DEBUG=False DJANGO_SECRET_KEY=collectstatic-build-only-not-used-at-runtime-0000000000 \
    python manage.py collectstatic --noinput

RUN useradd --create-home --uid 1000 magivents && mkdir -p /app/media && chown -R magivents /app/media
USER magivents

EXPOSE 8000
CMD ["gunicorn", "-c", "gunicorn_config.py"]
