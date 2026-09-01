from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.utils import timezone

from .models import ConsoleSession, Lab


def _notify_console_revoked(session_ids, reason):
    layer = get_channel_layer()
    if not layer:
        return
    for session_id in session_ids:
        try:
            async_to_sync(layer.group_send)(
                f"console.{session_id}",
                {"type": "access.revoked", "reason": reason},
            )
        except Exception:
            # Database revocation and the consumer's periodic authorization
            # check remain authoritative if a transient channel signal fails.
            continue


def revoke_project_access(project, user, *, reason="project_access_changed"):
    """Revoke active project-scoped access that outlives an HTTP request."""
    now = timezone.now()
    sessions = list(
        ConsoleSession.objects.filter(
            device__deployment__revision__lab__project=project,
            user=user,
            revoked_at__isnull=True,
            expires_at__gt=now,
        ).values_list("id", flat=True)
    )
    if sessions:
        ConsoleSession.objects.filter(id__in=sessions).update(revoked_at=now)
    released_leases = Lab.objects.filter(
        project=project,
        edit_lock_owner=user,
    ).update(
        edit_lock_owner=None,
        edit_lock_token_hash="",
        edit_lock_expires_at=None,
        updated_at=now,
    )
    transaction.on_commit(lambda: _notify_console_revoked(sessions, reason))
    return {"revoked_consoles": len(sessions), "released_edit_leases": released_leases}
