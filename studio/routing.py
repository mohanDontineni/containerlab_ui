from django.urls import re_path
from .consumers import ConsoleConsumer
websocket_urlpatterns=[re_path(r"^ws/consoles/(?P<session_id>[0-9a-f-]+)/$",ConsoleConsumer.as_asgi())]

