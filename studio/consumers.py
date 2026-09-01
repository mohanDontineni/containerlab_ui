import asyncio
import hashlib
import hmac
import json
from contextlib import suppress

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.db.models import Q
from django.utils import timezone
from kubernetes import client, config
from kubernetes.stream import stream

from .models import AuditEvent, ConsoleSession

IDLE_SECONDS = 15 * 60


class ConsoleConsumer(AsyncJsonWebsocketConsumer):
    transport = None
    pump_task = None

    async def connect(self):
        session = await self.authorize()
        if not session:
            await self.close(code=4403)
            return
        self.console = session
        self.console_group = f"console.{session['id']}"
        await self.channel_layer.group_add(self.console_group, self.channel_name)
        self.last_activity = asyncio.get_running_loop().time()
        self.next_authorization_check = self.last_activity
        await self.accept()
        await self.send_json({"type": "status", "state": "connecting", "readOnly": session["read_only"]})
        try:
            self.transport = await asyncio.to_thread(self.open_transport, session)
        except Exception as exc:
            await self.send_json({"type": "error", "message": f"Console connection failed: {str(exc)[:240]}"})
            await self.close(code=1011)
            return
        await self.record_event("console.connected")
        await self.send_json({"type": "status", "state": "connected", "readOnly": session["read_only"]})
        self.pump_task = asyncio.create_task(self.pump())

    @database_sync_to_async
    def authorize(self):
        user = self.scope.get("user")
        browser_key = self.scope.get("session").session_key if self.scope.get("session") else None
        if not user or not user.is_authenticated or not browser_key:
            return None
        session = ConsoleSession.objects.select_related(
            "device__lab_node__template_version", "device__deployment__revision__lab__project"
        ).filter(
            id=self.scope["url_route"]["kwargs"]["session_id"],
            user=user,
            expires_at__gt=timezone.now(),
            revoked_at__isnull=True,
        ).filter(
            Q(device__deployment__revision__lab__project__owner=user)
            | Q(device__deployment__revision__lab__project__memberships__user=user)
        ).first()
        if not session:
            return None
        expected = hashlib.sha256(f"{browser_key}:{session.id}".encode()).hexdigest()
        if not hmac.compare_digest(expected, session.token_hash):
            return None
        return {
            "id": str(session.id), "read_only": session.read_only,
            "namespace": session.device.deployment.namespace,
            "pod": session.device.runtime_resources.get("pod"),
            "node": session.device.lab_node.name,
            "kind": session.device.lab_node.template_version.containerlab_kind,
        }

    @database_sync_to_async
    def session_active(self):
        session = ConsoleSession.objects.select_related(
            "user", "device__deployment__revision__lab__project"
        ).filter(
            id=self.console["id"],
            expires_at__gt=timezone.now(),
            revoked_at__isnull=True,
        ).first()
        if not session:
            return False
        project = session.device.deployment.revision.lab.project
        if project.owner_id == session.user_id or session.user.is_superuser:
            return True
        role = project.memberships.filter(user_id=session.user_id).values_list("role", flat=True).first()
        return bool(role and (session.read_only or role in ("administrator", "editor")))

    @staticmethod
    def open_transport(session):
        config.load_incluster_config()
        api = client.CoreV1Api()
        command = ["docker", "exec", "-it", session["node"], "sh"]
        return stream(api.connect_get_namespaced_pod_exec, session["pod"], session["namespace"], command=command,
            stderr=True, stdin=True, stdout=True, tty=True, _preload_content=False)

    async def pump(self):
        try:
            while self.transport and self.transport.is_open():
                current = asyncio.get_running_loop().time()
                if current >= self.next_authorization_check:
                    self.next_authorization_check = current + 2
                    if not await self.session_active():
                        await self.send_json({"type": "status", "state": "access-revoked"})
                        await self.close(code=4403)
                        return
                if current - self.last_activity > IDLE_SECONDS:
                    await self.send_json({"type": "status", "state": "idle-timeout"})
                    await self.close(code=4408)
                    return
                await asyncio.to_thread(self.transport.update, timeout=1)
                if self.transport.peek_stdout():
                    await self.send_json({"type": "output", "data": self.transport.read_stdout()})
                if self.transport.peek_stderr():
                    await self.send_json({"type": "output", "data": self.transport.read_stderr()})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            with suppress(Exception):
                await self.send_json({"type": "error", "message": f"Console disconnected: {str(exc)[:240]}"})
        finally:
            with suppress(Exception):
                await self.close()

    async def receive_json(self, content, **kwargs):
        if not await self.authorize():
            await self.close(code=4403)
            return
        self.last_activity = asyncio.get_running_loop().time()
        message_type = content.get("type")
        if message_type == "input":
            if self.console["read_only"]:
                await self.send_json({"type": "error", "message": "Viewer console is read-only"})
                return
            data = str(content.get("data", ""))[:8192]
            await asyncio.to_thread(self.transport.write_stdin, data)
        elif message_type == "resize":
            columns = max(20, min(int(content.get("columns", 80)), 400))
            rows = max(5, min(int(content.get("rows", 24)), 200))
            payload = json.dumps({"Width": columns, "Height": rows})
            await asyncio.to_thread(self.transport.write_channel, 4, payload)
            await self.send_json({"type": "resize-ack", "columns": columns, "rows": rows})

    async def disconnect(self, code):
        if self.pump_task and self.pump_task is not asyncio.current_task():
            self.pump_task.cancel()
        if self.transport:
            with suppress(Exception):
                await asyncio.to_thread(self.transport.close)
        if getattr(self, "console_group", None):
            await self.channel_layer.group_discard(self.console_group, self.channel_name)
        if getattr(self, "console", None):
            await self.record_event("console.disconnected")

    async def access_revoked(self, event):
        with suppress(Exception):
            await self.send_json({"type": "status", "state": "access-revoked", "reason": event.get("reason", "project_access_changed")})
        await self.close(code=4403)

    @database_sync_to_async
    def record_event(self, action):
        session = ConsoleSession.objects.select_related("device__deployment__revision__lab__project").filter(id=self.console["id"]).first()
        if session:
            AuditEvent.objects.create(actor=session.user, project=session.device.deployment.revision.lab.project, action=action,
                target_type="ConsoleSession", target_id=session.id, correlation_id=str(session.id), metadata={})
