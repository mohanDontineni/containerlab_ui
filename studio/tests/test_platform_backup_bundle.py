import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest


spec=importlib.util.spec_from_file_location("platform_backup_bundle",Path(__file__).parents[2]/"scripts"/"platform_backup_bundle.py")
bundle=importlib.util.module_from_spec(spec);spec.loader.exec_module(bundle)


def write_tar(path,name="payload.txt",content=b"payload"):
    with tarfile.open(path,"w:gz") as archive:
        info=tarfile.TarInfo(name);info.size=len(content);archive.addfile(info,io.BytesIO(content))


def valid_bundle(tmp_path):
    (tmp_path/"database.dump").write_bytes(b"PGDMP\x01database")
    for name in ("artifacts.tar.gz","registry.tar.gz","redis.tar.gz"): write_tar(tmp_path/name)
    for name in ("platform-secret.json","gateway-tls-secret.json"):
        (tmp_path/name).write_text(json.dumps({"apiVersion":"v1","kind":"Secret","metadata":{"name":name},"data":{"key":"dmFsdWU="}}))
    (tmp_path/"platform-deployments.json").write_text(json.dumps({"items":[{"metadata":{"name":"containerlab-studio-web"},
        "spec":{"template":{"spec":{"containers":[{"image":"containerlab-studio:test"}]}}}}]}))
    return tmp_path


def test_platform_backup_manifest_verifies_all_payloads_and_namespace(tmp_path):
    root=valid_bundle(tmp_path);manifest=bundle.create(root,"containerlab")
    verified=bundle.verify(root,"containerlab")
    assert verified==manifest and set(verified["files"])==set(bundle.PAYLOADS)
    assert (root/"SHA256SUMS").read_text().count("\n")==len(bundle.PAYLOADS)+1
    with pytest.raises(ValueError,match="namespace"): bundle.verify(root,"other-namespace")


def test_platform_backup_verification_rejects_tampering(tmp_path):
    root=valid_bundle(tmp_path);bundle.create(root,"containerlab")
    with (root/"artifacts.tar.gz").open("ab") as output: output.write(b"tampered")
    with pytest.raises(ValueError,match="integrity check failed"): bundle.verify(root,"containerlab")


def test_platform_backup_creation_rejects_traversal_and_links(tmp_path):
    root=valid_bundle(tmp_path)
    write_tar(root/"registry.tar.gz","../../escape")
    with pytest.raises(ValueError,match="unsafe archive member"): bundle.create(root,"containerlab")
    write_tar(root/"registry.tar.gz")
    with tarfile.open(root/"redis.tar.gz","w:gz") as archive:
        info=tarfile.TarInfo("unsafe-link");info.type=tarfile.SYMTYPE;info.linkname="/etc/passwd";archive.addfile(info)
    with pytest.raises(ValueError,match="unsafe archive member"): bundle.create(root,"containerlab")
