from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async
from django.utils import timezone
from .models import ConsoleSession

class ConsoleConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.session=await self.authorize()
        if not self.session: await self.close(code=4403); return
        await self.accept(); await self.send_json({"type":"status","state":"authorized","readOnly":self.session.read_only})
    @database_sync_to_async
    def authorize(self):
        user=self.scope.get("user")
        if not user or not user.is_authenticated:return None
        return ConsoleSession.objects.select_related("device__deployment__revision__lab__project").filter(id=self.scope["url_route"]["kwargs"]["session_id"],user=user,expires_at__gt=timezone.now(),revoked_at__isnull=True).first()
    async def receive_json(self,content,**kwargs):
        # Transport binding is deliberately unavailable until a verified template console target exists.
        if content.get("type")=="resize": await self.send_json({"type":"resize-ack"})
        elif self.session.read_only: await self.send_json({"type":"error","message":"Viewer console is read-only"})
        else: await self.send_json({"type":"error","message":"Device transport is not connected"})

