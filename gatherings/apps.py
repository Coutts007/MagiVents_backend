from django.apps import AppConfig


class GatheringsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'gatherings'

    def ready(self):
        from . import signals  # noqa: F401 - registers the catalogue cache invalidation
