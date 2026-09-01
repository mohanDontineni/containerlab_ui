import hashlib,io,json,tarfile
from pathlib import Path
import pytest
from django.utils import timezone
from studio.models import AuditEvent,ImageArtifact,User,Project,UploadSession
from studio.quotas import project_usage
from studio.uploads import UploadError,append_chunk,cleanup_stale_uploads,finalize,inspect_file

@pytest.mark.django_db
def test_stale_upload_cleanup_is_bounded_safe_idempotent_audited_and_releases_reservation(tmp_path,settings):
    settings.MEDIA_ROOT=tmp_path;user=User.objects.create_user("expiry-owner",password="long-enough-password");project=Project.objects.create(owner=user,name="expiry")
    quarantine=tmp_path/"quarantine";quarantine.mkdir();now=timezone.now()
    expired_path=quarantine/"expired";expired_path.write_bytes(b"partial")
    failed_path=quarantine/"failed";failed_path.write_bytes(b"bad checksum")
    outside=tmp_path/"outside";outside.write_bytes(b"must remain")
    future_path=quarantine/"future";future_path.write_bytes(b"resumable")
    expired=UploadSession.objects.create(owner=user,project=project,original_filename="expired.tar",expected_size=100,received_bytes=7,
        expires_at=now-timezone.timedelta(minutes=1),artifact_destination=str(expired_path))
    failed=UploadSession.objects.create(owner=user,project=project,original_filename="failed.tar",expected_size=12,received_bytes=12,status=UploadSession.Status.FAILED,
        expires_at=now-timezone.timedelta(minutes=1),artifact_destination=str(failed_path))
    unsafe=UploadSession.objects.create(owner=user,project=project,original_filename="outside.tar",expected_size=11,received_bytes=11,
        expires_at=now-timezone.timedelta(minutes=1),artifact_destination=str(outside))
    future=UploadSession.objects.create(owner=user,project=project,original_filename="future.tar",expected_size=200,received_bytes=9,
        expires_at=now+timezone.timedelta(hours=1),artifact_destination=str(future_path))
    assert project_usage(project)["reserved_upload_bytes"]==200
    cleaned=cleanup_stale_uploads(now=now,limit=10)
    assert set(cleaned)=={str(expired.id),str(failed.id),str(unsafe.id)} and cleanup_stale_uploads(now=now,limit=10)==[]
    expired.refresh_from_db();failed.refresh_from_db();unsafe.refresh_from_db();future.refresh_from_db()
    assert expired.status==UploadSession.Status.EXPIRED and expired.cleanup_result["storage_removed"] is True and not expired_path.exists()
    assert failed.status==UploadSession.Status.FAILED and failed.cleanup_result["storage_removed"] is True and not failed_path.exists()
    assert unsafe.status==UploadSession.Status.EXPIRED and unsafe.cleanup_result["storage_removed"] is False and outside.exists()
    assert future.status==UploadSession.Status.ACTIVE and not future.cleanup_result and future_path.exists()
    assert AuditEvent.objects.filter(action="image.upload_expired",project=project).count()==2
    assert AuditEvent.objects.filter(action="image.upload_quarantine_cleaned",target_id=failed.id,metadata__storage_removed=True).exists()
    assert project_usage(project)["reserved_upload_bytes"]==200

@pytest.mark.django_db
def test_chunk_offset_and_checksum(tmp_path,settings):
    settings.MEDIA_ROOT=tmp_path; u=User.objects.create_user("u",password="long-enough-password"); p=Project.objects.create(owner=u,name="p"); data=b"bad-format"
    s=UploadSession.objects.create(owner=u,project=p,original_filename="x.bin",expected_size=len(data),expected_checksum=hashlib.sha256(data).hexdigest(),expires_at=timezone.now()+timezone.timedelta(hours=1),artifact_destination=str(tmp_path/"q"))
    append_chunk(s,u,0,io.BytesIO(data)); s.refresh_from_db()
    with pytest.raises(UploadError): append_chunk(s,u,0,io.BytesIO(b"x"))
    artifact=finalize(s,u); assert artifact.validation_status=="unsupported"
@pytest.mark.django_db
def test_checksum_mismatch_fails(tmp_path):
    u=User.objects.create_user("u2",password="long-enough-password"); p=Project.objects.create(owner=u,name="p"); data=b"data"
    s=UploadSession.objects.create(owner=u,project=p,original_filename="x",expected_size=4,expected_checksum="0"*64,expires_at=timezone.now()+timezone.timedelta(hours=1),artifact_destination=str(tmp_path/"q"))
    append_chunk(s,u,0,io.BytesIO(data))
    with pytest.raises(UploadError,match="checksum"): finalize(s,u)
@pytest.mark.django_db
def test_server_computes_optional_checksum_and_deduplicates_within_project(tmp_path):
    u=User.objects.create_user("optional",password="long-enough-password");p=Project.objects.create(owner=u,name="p");data=b"same-image"
    def session(name): return UploadSession.objects.create(owner=u,project=p,original_filename=name,expected_size=len(data),expected_checksum="",
        expires_at=timezone.now()+timezone.timedelta(hours=1),artifact_destination=str(tmp_path/name))
    first=session("one.bin");append_chunk(first,u,0,io.BytesIO(data));artifact=finalize(first,u)
    second=session("two.bin");append_chunk(second,u,0,io.BytesIO(data));duplicate=finalize(second,u)
    assert duplicate.id==artifact.id and ImageArtifact.objects.filter(project=p).count()==1
    second.refresh_from_db();assert second.computed_checksum==hashlib.sha256(data).hexdigest() and not Path(second.artifact_destination).exists()

@pytest.mark.django_db
def test_duplicate_unsupported_artifact_is_reinspected_after_validator_upgrade(tmp_path,monkeypatch):
    user=User.objects.create_user("reinspect",password="long-enough-password");project=Project.objects.create(owner=user,name="p")
    data=b"future-supported-image";checksum=hashlib.sha256(data).hexdigest()
    first=UploadSession.objects.create(owner=user,project=project,original_filename="first",expected_size=len(data),expected_checksum=checksum,
        expires_at=timezone.now()+timezone.timedelta(hours=1),artifact_destination=str(tmp_path/"first"))
    append_chunk(first,user,0,io.BytesIO(data));artifact=finalize(first,user)
    assert artifact.validation_status==ImageArtifact.Validation.UNSUPPORTED
    second=UploadSession.objects.create(owner=user,project=project,original_filename="second",expected_size=len(data),expected_checksum=checksum,
        expires_at=timezone.now()+timezone.timedelta(hours=1),artifact_destination=str(tmp_path/"second"))
    append_chunk(second,user,0,io.BytesIO(data))
    monkeypatch.setattr("studio.uploads.inspect_file",lambda _:("docker-archive",{"deployable":True,"architecture":"amd64","import_source":"sha256:"+"a"*64,"image_count":1}))
    duplicate=finalize(second,user);duplicate.refresh_from_db()
    assert duplicate.id==artifact.id and duplicate.validation_status==ImageArtifact.Validation.VALIDATED
    assert duplicate.architecture=="amd64" and not Path(second.artifact_destination).exists()
@pytest.mark.django_db
def test_oversized_chunk_rolls_back_file_and_offset(tmp_path):
    u=User.objects.create_user("overflow",password="long-enough-password");p=Project.objects.create(owner=u,name="p")
    s=UploadSession.objects.create(owner=u,project=p,original_filename="x",expected_size=4,expected_checksum="",expires_at=timezone.now()+timezone.timedelta(hours=1),artifact_destination=str(tmp_path/"q"))
    with pytest.raises(UploadError,match="exceeds"): append_chunk(s,u,0,io.BytesIO(b"12345"))
    s.refresh_from_db();assert s.received_bytes==0 and Path(s.artifact_destination).stat().st_size==0
def test_traversal_tar_rejected(tmp_path):
    path=tmp_path/"evil.tar"
    with tarfile.open(path,"w") as tf:
        info=tarfile.TarInfo("../../escape"); info.size=1; tf.addfile(info,io.BytesIO(b"x"))
    kind,result=inspect_file(path); assert kind=="unsafe-archive" and not result["deployable"]

def _docker_archive(path, config_member_name):
    configuration=json.dumps({"architecture":"amd64"},separators=(",",":")).encode()
    digest=hashlib.sha256(configuration).hexdigest()
    config_name=config_member_name.format(digest=digest)
    manifest=json.dumps([{"Config":config_name,"RepoTags":["example/firewall:1"],"Layers":[]}]).encode()
    with tarfile.open(path,"w") as tf:
        for name,payload in ((config_name,configuration),("manifest.json",manifest)):
            info=tarfile.TarInfo(name);info.size=len(payload);tf.addfile(info,io.BytesIO(payload))
    return digest

@pytest.mark.parametrize("config_name",["{digest}.json","sha256:{digest}"])
def test_docker_archive_accepts_verified_standard_and_kaniko_config_names(tmp_path,config_name):
    path=tmp_path/"image.tar";digest=_docker_archive(path,config_name)
    kind,result=inspect_file(path)
    assert kind=="docker-archive" and result=={"deployable":True,"architecture":"amd64","import_source":f"sha256:{digest}","image_count":1}

def test_docker_archive_rejects_configuration_digest_mismatch(tmp_path):
    path=tmp_path/"image.tar";_docker_archive(path,"0"*64+".json")
    kind,result=inspect_file(path)
    assert kind=="docker-archive" and result["deployable"] is False
    assert result["reason"]=="Image configuration digest mismatch"
