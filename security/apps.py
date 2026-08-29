from django.apps import AppConfig


class SecurityConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'security'

    def ready(self):
        # Import registers the auth signal receivers (login/logout/failed).
        from . import signals  # noqa: F401
