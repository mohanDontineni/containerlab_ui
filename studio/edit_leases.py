import hashlib
import secrets
from datetime import timedelta

from django.utils import timezone

LEASE_SECONDS = 300
TOKEN_HEADER = "X-Edit-Lease"


def token_hash(token):
    return hashlib.sha256(str(token or "").encode()).hexdigest()


def is_active(lab, now=None):
    now = now or timezone.now()
    return bool(lab.edit_lock_owner_id and lab.edit_lock_token_hash and lab.edit_lock_expires_at and lab.edit_lock_expires_at > now)


def owner_name(lab):
    owner = lab.edit_lock_owner
    if not owner:
        return None
    return owner.get_full_name().strip() or owner.get_username()


def status_payload(lab, user, token=None):
    active = is_active(lab)
    owns = active and lab.edit_lock_owner_id == user.id and bool(token) and secrets.compare_digest(lab.edit_lock_token_hash, token_hash(token))
    return {"active": active, "can_edit": owns, "owner": owner_name(lab) if active else None,
            "expires_at": lab.edit_lock_expires_at.isoformat() if active else None, "lease_seconds": LEASE_SECONDS}


def acquire(lab, user, supplied_token=None):
    now = timezone.now()
    if is_active(lab, now) and lab.edit_lock_owner_id != user.id:
        return None, status_payload(lab, user, supplied_token)
    if is_active(lab, now) and supplied_token and secrets.compare_digest(lab.edit_lock_token_hash, token_hash(supplied_token)):
        token = supplied_token
    else:
        token = secrets.token_urlsafe(32)
        lab.edit_lock_token_hash = token_hash(token)
    lab.edit_lock_owner = user
    lab.edit_lock_expires_at = now + timedelta(seconds=LEASE_SECONDS)
    lab.save(update_fields=["edit_lock_owner", "edit_lock_token_hash", "edit_lock_expires_at", "updated_at"])
    payload = status_payload(lab, user, token)
    payload["token"] = token
    return token, payload


def valid_token(lab, user, token):
    return bool(is_active(lab) and lab.edit_lock_owner_id == user.id and token and
                secrets.compare_digest(lab.edit_lock_token_hash, token_hash(token)))


def conflict_payload(lab):
    return {"error": {"code": "edit_lease_required", "details": "This topology is open in another editing session.",
                      "owner": owner_name(lab), "expires_at": lab.edit_lock_expires_at.isoformat() if lab.edit_lock_expires_at else None}}


def release(lab):
    lab.edit_lock_owner = None
    lab.edit_lock_token_hash = ""
    lab.edit_lock_expires_at = None
    lab.save(update_fields=["edit_lock_owner", "edit_lock_token_hash", "edit_lock_expires_at", "updated_at"])
