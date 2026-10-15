"""
Gunicorn settings for the AI assistant service in production (docker-compose.prod.yml, service `assistant`).

It runs the same Django project as ASGI under Uvicorn workers; nginx sends it /api/assistant/ only. An answer
streams on the worker's event loop and holds no thread while it waits for the AI provider, so a few processes
keep thousands of answers open at once.
"""
import multiprocessing
import os

wsgi_app = 'config.asgi:application'
worker_class = 'uvicorn_worker.UvicornWorker'
bind = os.environ.get('GUNICORN_BIND', '0.0.0.0:8000')
# One process per CPU core: the work is mostly waiting, and each process serves many answers at once
workers = int(os.environ.get('WEB_CONCURRENCY') or max(2, multiprocessing.cpu_count()))
backlog = int(os.environ.get('GUNICORN_BACKLOG', '4096'))
# A worker that stops reporting for this long is restarted
timeout = 60
# On a restart or deploy, answers already streaming get time to finish (AI_TIMEOUT_SECONDS is 30 by default)
graceful_timeout = 45
keepalive = 75
max_requests = 20000
max_requests_jitter = 2000
accesslog = '-'


def post_worker_init(worker):
    # Load the AI SDK while the process starts. Importing it takes about 2 seconds, and doing that during the
    # first assistant request would stall every request on the process.
    import anthropic  # noqa: F401
    import assistant.providers.claude  # noqa: F401
