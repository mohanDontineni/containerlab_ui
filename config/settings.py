import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "unsafe-test-only-key")
DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"
ALLOWED_HOSTS = [x for x in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if x]
CSRF_TRUSTED_ORIGINS = [x for x in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if x]
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "rest_framework", "django_filters", "channels", "drf_spectacular", "studio",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware", "whitenoise.middleware.WhiteNoiseMiddleware",
    "studio.middleware.CorrelationIdMiddleware", "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware", "django.contrib.messages.middleware.MessageMiddleware",
    "studio.middleware.ForcedPasswordChangeMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [BASE_DIR / "templates"], "APP_DIRS": True,
              "OPTIONS": {"context_processors": ["django.template.context_processors.request", "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages"]}}]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
if os.environ.get("POSTGRES_HOST"):
    DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": os.environ.get("POSTGRES_DB", "containerlab"),
        "USER": os.environ.get("POSTGRES_USER", "containerlab"), "PASSWORD": os.environ["POSTGRES_PASSWORD"],
        "HOST": os.environ["POSTGRES_HOST"], "PORT": os.environ.get("POSTGRES_PORT", "5432"), "CONN_MAX_AGE": 60}}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
AUTH_PASSWORD_VALIDATORS = [
    {"NAME":"django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME":"django.contrib.auth.password_validation.MinimumLengthValidator","OPTIONS":{"min_length":12}},
    {"NAME":"django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME":"django.contrib.auth.password_validation.NumericPasswordValidator"},
]
AUTH_USER_MODEL = "studio.User"
LOGIN_REDIRECT_URL = "/"
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_ROOT = Path(os.environ.get("ARTIFACT_ROOT", BASE_DIR / "media"))
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
REST_FRAMEWORK = {"DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"], "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination", "PAGE_SIZE": 50,
    "EXCEPTION_HANDLER": "studio.api.exception_handler"}
SPECTACULAR_SETTINGS = {"TITLE": "ContainerLab Studio API", "VERSION": "1.0.0"}
redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": redis_url}}
CHANNEL_LAYERS = {"default": {"BACKEND": "channels_redis.core.RedisChannelLayer", "CONFIG": {"hosts": [redis_url]}}}
CELERY_BROKER_URL = redis_url
CELERY_RESULT_BACKEND = redis_url
CELERY_TASK_TRACK_STARTED = True
CELERY_BEAT_SCHEDULE = {
    "reconcile-active-deployments": {
        "task": "studio.tasks.reconcile_active_deployments",
        "schedule": 30.0,
    },
    "dispatch-due-deployment-schedules": {"task":"studio.tasks.dispatch_due_schedules","schedule":15.0},
    "expire-stale-image-uploads": {"task":"studio.tasks.expire_stale_uploads","schedule":900.0},
    "probe-internal-oci-registry": {"task":"studio.tasks.probe_registry_health","schedule":30.0},
    "probe-platform-network-isolation": {"task":"studio.tasks.probe_network_isolation","schedule":30.0},
}
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(50 * 1024**3)))
UPLOAD_CHUNK_BYTES = int(os.environ.get("UPLOAD_CHUNK_BYTES", str(16 * 1024**2)))
CLUSTER_IDENTITY = os.environ.get("CLUSTER_IDENTITY", "unconfigured")
LAB_NAMESPACE_PREFIX = os.environ.get("LAB_NAMESPACE_PREFIX", "containerlab-lab-")
STUDIO_NAMESPACE = os.environ.get("STUDIO_NAMESPACE", "containerlab")
PUBLISHER_IMAGE = os.environ.get("PUBLISHER_IMAGE", "ghcr.io/clabernetes/clabernetes/clabernetes-launcher:0.8.0")
PUBLISHER_TIMEOUT_SECONDS = int(os.environ.get("PUBLISHER_TIMEOUT_SECONDS", "180"))
PUBLISHER_NODE_SELECTOR = {"kubernetes.io/hostname": os.environ["PUBLISHER_NODE_NAME"]} if os.environ.get("PUBLISHER_NODE_NAME") else {}
REGISTRY_INTERNAL_URL = os.environ.get("REGISTRY_INTERNAL_URL", "")
