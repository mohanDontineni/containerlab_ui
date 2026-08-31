import hashlib
import json
import os
import tarfile
from pathlib import Path
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import ImageArtifact, UploadSession

class UploadError(ValueError): pass

@transaction.atomic
def append_chunk(session, owner, offset, stream):
    session=UploadSession.objects.select_for_update().get(pk=session.pk)
    if session.owner_id != owner.id: raise PermissionError("Upload session owner mismatch")
    if session.status != UploadSession.Status.ACTIVE or session.expires_at <= timezone.now(): raise UploadError("Upload session is not active")
    if offset != session.received_bytes: raise UploadError(f"Expected offset {session.received_bytes}")
    path=Path(session.artifact_destination); path.parent.mkdir(parents=True,exist_ok=True)
    written=0
    with path.open("ab+") as output:
        try:
            for chunk in iter(lambda: stream.read(1024*1024), b""):
                written+=len(chunk)
                if offset+written>session.expected_size: raise UploadError("Chunk exceeds declared size")
                output.write(chunk)
            output.flush(); os.fsync(output.fileno())
        except Exception:
            output.truncate(offset)
            raise
    session.received_bytes+=written; session.received_parts+=1; session.save(update_fields=["received_bytes","received_parts","updated_at"])
    return written

def inspect_file(path):
    with open(path,"rb") as f: magic=f.read(512)
    if magic[:4] in (b"QFI\xfb",): return "qcow2", {"deployable":False,"reason":"VM disk requires an approved device recipe and KVM"}
    if magic[0:6] in (b"070701",b"070702"): return "cpio", {"deployable":False,"reason":"Raw root filesystem archives require an approved recipe"}
    if tarfile.is_tarfile(path):
        with tarfile.open(path,"r:*") as tf:
            names=tf.getnames()
            unsafe=[n for n in names if n.startswith("/") or ".." in Path(n).parts]
            if unsafe: return "unsafe-archive", {"deployable":False,"reason":"Archive contains traversal paths"}
            if "manifest.json" in names:
                member=tf.getmember("manifest.json")
                if member.size>1024*1024: return "docker-archive",{"deployable":False,"reason":"Docker manifest exceeds the inspection limit"}
                try:
                    manifest=json.load(tf.extractfile(member))
                    if len(manifest)!=1: raise ValueError("Archive must contain exactly one image")
                    config_name=manifest[0]["Config"];config_member=tf.getmember(config_name)
                    if config_member.size>1024*1024: raise ValueError("Image configuration exceeds the inspection limit")
                    configuration=json.load(tf.extractfile(config_member));config_digest=Path(config_name).name
                    if len(config_digest)!=64 or any(c not in "0123456789abcdef" for c in config_digest.lower()): raise ValueError("Invalid image configuration digest")
                    architecture=configuration.get("architecture","")
                    if architecture not in ("amd64","arm64"): raise ValueError(f"Unsupported architecture: {architecture or 'unknown'}")
                    return "docker-archive", {"deployable":True,"architecture":architecture,"import_source":f"sha256:{config_digest}","image_count":1}
                except (KeyError,TypeError,ValueError,json.JSONDecodeError,tarfile.TarError) as exc:
                    return "docker-archive",{"deployable":False,"reason":str(exc)}
            if "index.json" in names and "oci-layout" in names:
                try:
                    member=tf.getmember("index.json")
                    if member.size>1024*1024: raise ValueError("OCI index exceeds the inspection limit")
                    index=json.load(tf.extractfile(member));manifests=index.get("manifests",[])
                    if len(manifests)!=1: raise ValueError("Archive must contain exactly one image manifest")
                    digest=manifests[0].get("digest","")
                    if not digest.startswith("sha256:") or len(digest)!=71: raise ValueError("OCI manifest is not SHA-256 addressed")
                    architecture=manifests[0].get("platform",{}).get("architecture","")
                    if architecture and architecture not in ("amd64","arm64"): raise ValueError(f"Unsupported architecture: {architecture}")
                    return "oci-archive", {"deployable":True,"architecture":architecture or "amd64","import_source":digest,"image_count":1}
                except (KeyError,TypeError,ValueError,json.JSONDecodeError,tarfile.TarError) as exc:
                    return "oci-archive",{"deployable":False,"reason":str(exc)}
            return "tar-archive", {"deployable":False,"reason":"Tar archive is neither OCI nor Docker format"}
    return "unknown", {"deployable":False,"reason":"Unsupported or malformed image format"}

@transaction.atomic
def finalize(session, owner):
    session=UploadSession.objects.select_for_update().get(pk=session.pk)
    if session.owner_id!=owner.id: raise PermissionError
    if session.received_bytes!=session.expected_size: raise UploadError("Upload is incomplete")
    digest=hashlib.sha256()
    with open(session.artifact_destination,"rb") as f:
        for chunk in iter(lambda:f.read(4*1024*1024),b""): digest.update(chunk)
    checksum=digest.hexdigest()
    if session.expected_checksum and checksum.lower()!=session.expected_checksum.lower(): session.status=UploadSession.Status.FAILED; session.computed_checksum=checksum; session.save(); raise UploadError("SHA-256 checksum mismatch")
    existing=ImageArtifact.objects.filter(project=session.project,checksum=checksum).first()
    if existing:
        Path(session.artifact_destination).unlink(missing_ok=True)
        session.status=UploadSession.Status.COMPLETE; session.computed_checksum=checksum; session.save()
        return existing
    detected,result=inspect_file(session.artifact_destination)
    artifact=ImageArtifact.objects.create(project=session.project,owner=owner,upload_session=session,original_filename=session.original_filename,
        detected_format=detected,byte_size=session.expected_size,checksum=checksum,storage_reference=session.artifact_destination,
        architecture=result.get("architecture",""),license_acknowledged=session.license_acknowledged,inspection_result=result,
        validation_status=ImageArtifact.Validation.VALIDATED if result["deployable"] else ImageArtifact.Validation.UNSUPPORTED)
    session.status=UploadSession.Status.COMPLETE; session.computed_checksum=checksum; session.save()
    return artifact
