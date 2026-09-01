#!/usr/bin/env python3
"""Create and verify integrity metadata for a ContainerLab Studio platform backup."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile
from datetime import datetime, timezone

SCHEMA=1
PAYLOADS=("database.dump","artifacts.tar.gz","registry.tar.gz","redis.tar.gz",
    "platform-secret.json","gateway-tls-secret.json","platform-deployments.json")

def digest(path):
    hasher=hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda:source.read(4*1024*1024),b""): hasher.update(chunk)
    return hasher.hexdigest()

def safe_tar(path):
    with tarfile.open(path,"r:gz") as archive:
        for member in archive.getmembers():
            name=PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or member.issym() or member.islnk():
                raise ValueError(f"unsafe archive member in {path.name}: {member.name}")

def create(directory,namespace):
    root=Path(directory).resolve();missing=[name for name in PAYLOADS if not (root/name).is_file()]
    if missing: raise ValueError(f"missing backup payloads: {', '.join(missing)}")
    if (root/"database.dump").read_bytes()[:5]!=b"PGDMP": raise ValueError("database.dump is not a PostgreSQL custom archive")
    for name in ("artifacts.tar.gz","registry.tar.gz","redis.tar.gz"): safe_tar(root/name)
    files={name:{"sha256":digest(root/name),"bytes":(root/name).stat().st_size} for name in PAYLOADS}
    manifest={"schema":SCHEMA,"product":"containerlab-studio","created_at":datetime.now(timezone.utc).isoformat(),
        "namespace":namespace,"consistency":"quiesced","files":files}
    (root/"manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    lines=[f"{files[name]['sha256']}  {name}" for name in PAYLOADS]
    lines.append(f"{digest(root/'manifest.json')}  manifest.json")
    (root/"SHA256SUMS").write_text("\n".join(lines)+"\n",encoding="ascii")
    return manifest

def verify(directory,namespace=None):
    root=Path(directory).resolve();manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema")!=SCHEMA or manifest.get("product")!="containerlab-studio" or manifest.get("consistency")!="quiesced":
        raise ValueError("unsupported or non-quiesced platform backup manifest")
    if namespace and manifest.get("namespace")!=namespace: raise ValueError("backup namespace does not match the restore target")
    if set(manifest.get("files",{}))!=set(PAYLOADS): raise ValueError("backup payload inventory is incomplete or unexpected")
    observed={path.name for path in root.iterdir()}
    allowed=set(PAYLOADS)|{"manifest.json","SHA256SUMS"}
    if observed!=allowed: raise ValueError("backup directory contains missing or unexpected files")
    for name in PAYLOADS:
        path=root/name
        if not path.is_file() or path.is_symlink(): raise ValueError(f"backup payload is missing or unsafe: {name}")
        expected=manifest["files"][name]
        if path.stat().st_size!=expected.get("bytes") or digest(path)!=expected.get("sha256"):
            raise ValueError(f"backup payload integrity check failed: {name}")
    if (root/"database.dump").read_bytes()[:5]!=b"PGDMP": raise ValueError("database.dump is not a PostgreSQL custom archive")
    for name in ("artifacts.tar.gz","registry.tar.gz","redis.tar.gz"): safe_tar(root/name)
    for name in ("platform-secret.json","gateway-tls-secret.json"):
        secret=json.loads((root/name).read_text(encoding="utf-8"))
        if secret.get("kind")!="Secret" or not secret.get("data"): raise ValueError(f"invalid secret payload: {name}")
    deployments=json.loads((root/"platform-deployments.json").read_text(encoding="utf-8"))
    web=next((item for item in deployments.get("items",[]) if item.get("metadata",{}).get("name")=="containerlab-studio-web"),None)
    if not web or not web.get("spec",{}).get("template",{}).get("spec",{}).get("containers",[{}])[0].get("image"):
        raise ValueError("platform deployment metadata does not contain the web restore image")
    return manifest

def sanitize_secret():
    source=json.load(sys.stdin);metadata=source.get("metadata",{})
    result={"apiVersion":"v1","kind":"Secret","metadata":{"name":metadata.get("name"),"namespace":metadata.get("namespace")},
        "type":source.get("type","Opaque"),"data":source.get("data",{})}
    if not result["metadata"]["name"] or not result["data"]: raise ValueError("secret export is empty")
    json.dump(result,sys.stdout,indent=2,sort_keys=True);sys.stdout.write("\n")

if __name__=="__main__":
    try:
        command=sys.argv[1]
        if command=="create" and len(sys.argv)==4: result=create(sys.argv[2],sys.argv[3]);print(json.dumps({"verified":True,"files":len(result["files"])}))
        elif command=="verify" and len(sys.argv) in (3,4): result=verify(sys.argv[2],sys.argv[3] if len(sys.argv)==4 else None);print(json.dumps({"verified":True,"schema":result["schema"],"namespace":result["namespace"],"files":len(result["files"])}))
        elif command=="sanitize-secret" and len(sys.argv)==2: sanitize_secret()
        else: raise ValueError("usage: platform_backup_bundle.py create DIR NAMESPACE | verify DIR [NAMESPACE] | sanitize-secret")
    except (IndexError,ValueError,OSError,json.JSONDecodeError,tarfile.TarError) as exc:
        print(f"backup bundle error: {exc}",file=sys.stderr);raise SystemExit(2)
