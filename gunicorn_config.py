"""
Gunicorn settings for the main API in production (the Dockerfile and docker-compose.prod.yml use this file).

Ordinary API requests are short, so they run as WSGI, the cheapest way to serve Django (about a third of the CPU
per request of ASGI). The AI assistant, whose answers stream for seconds, is served separately under ASGI: see
gunicorn_assistant.py.
"""
import multiprocessing
import os

wsgi_app = 'config.wsgi:application'
worker_class = 'gthread'
bind = os.environ.get('GUNICORN_BIND', '0.0.0.0:8000')
workers = int(os.environ.get('WEB_CONCURRENCY') or multiprocessing.cpu_count() + 1)
# Requests a process works on at once; a request waiting on the database or a payment provider frees the CPU
threads = int(os.environ.get('GUNICORN_THREADS', '4'))
# Connections waiting to be accepted during a burst
backlog = int(os.environ.get('GUNICORN_BACKLOG', '2048'))
timeout = 60
graceful_timeout = 30
# Longer than nginx keeps idle upstream connections, so nginx never reuses one the server has just closed
keepalive = 75
# Recycle each process now and then so memory cannot creep up; the jitter keeps them from restarting together
max_requests = 20000
max_requests_jitter = 2000
accesslog = '-'
