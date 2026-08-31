import hashlib,io,tarfile
from pathlib import Path
import pytest
from django.utils import timezone
from studio.models import ImageArtifact,User,Project,UploadSession
from studio.uploads import UploadError,append_chunk,finalize,inspect_file

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
