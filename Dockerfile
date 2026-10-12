# MagiVents API image. Production runs Gunicorn; docker-compose.yml (development) overrides the command.
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
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60", \
     "--access-logfile", "-"]
