import hashlib,io,tarfile
from pathlib import Path
import pytest
from django.utils import timezone
from studio.models import User,Project,UploadSession
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
def test_traversal_tar_rejected(tmp_path):
    path=tmp_path/"evil.tar"
    with tarfile.open(path,"w") as tf:
        info=tarfile.TarInfo("../../escape"); info.size=1; tf.addfile(info,io.BytesIO(b"x"))
    kind,result=inspect_file(path); assert kind=="unsafe-archive" and not result["deployable"]

