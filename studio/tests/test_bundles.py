import json
import uuid

import pytest
from django.db.models import F
from rest_framework.test import APIClient

from studio.bundles import export_lab_bundle
from studio.configurations import decrypt_configuration, encrypt_configuration
from studio.models import (AuditEvent, ConfigurationVersion, DeviceTemplateVersion,
                           ImageArtifact, Lab, LabInterface, LabLink, LabNode,
                           LabRevision, Project, ProjectMembership, PublishedImage, User)


def _lab(owner, name="portable"):
    project=Project.objects.create(owner=owner,name=f"project-{name}")
    lab=Lab.objects.create(project=project,name=name)
    template=DeviceTemplateVersion.objects.get(template__name="Linux Host",template__active_version_id=F("id"))
    artifact=ImageArtifact.objects.create(project=project,owner=owner,source_type="registry",original_filename="alpine",
        detected_format="oci-registry",byte_size=0,checksum=uuid.uuid4().hex.ljust(64,"0")[:64],architecture="amd64",
        storage_reference="docker.io/alpine",validation_status="validated")
    image=PublishedImage.objects.create(artifact=artifact,registry_digest="docker.io/alpine@sha256:"+uuid.uuid4().hex*2,
        repository="docker.io/alpine",architecture="amd64")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="a"*64)
    lab.current_draft=revision; lab.save(update_fields=["current_draft"])
    return lab,template,image,revision


@pytest.mark.django_db
def test_configuration_content_is_encrypted_and_authenticated():
    content="hostname edge-1\ninterface eth1\n ip address 10.0.0.1/24"
    encrypted=encrypt_configuration(content)
    assert content.encode() not in encrypted
    assert decrypt_configuration(encrypted)==content


@pytest.mark.django_db
def test_lab_bundle_round_trip_preserves_topology_and_configuration():
    owner=User.objects.create_user("bundle-owner",password="long-enough-password")
    lab,template,image,revision=_lab(owner)
    config=ConfigurationVersion.objects.create(project=lab.project,name="portable/a/startup",version=1,
        encrypted_content=encrypt_configuration("hostname a"),checksum="c"*64,created_by=owner)
    a=LabNode.objects.create(revision=revision,name="a",template_version=template,published_image=image,position={"x":10,"y":20},startup_configuration=config)
    b=LabNode.objects.create(revision=revision,name="b",template_version=template,published_image=image,position={"x":200,"y":20})
    rules=template.interface_rules; start=int(rules.get("start",1)); count=min(int(rules.get("count",4)),64); prefix=rules.get("prefix","eth")
    interfaces={}
    for node in (a,b):
        for number in range(start,start+count): interfaces[(node.name,number)]=LabInterface.objects.create(node=node,name=f"{prefix}{number}")
    LabLink.objects.create(revision=revision,endpoint_a=interfaces[("a",start)],endpoint_b=interfaces[("b",start)],label="uplink")
    client=APIClient(); client.force_authenticate(owner)
    exported=client.get(f"/api/v1/labs/{lab.id}/export/")
    assert exported.status_code==200 and exported["Content-Disposition"].endswith('.clabstudio.json"')
    bundle=json.loads(exported.content); assert bundle["topology"]["nodes"][0]["startupConfiguration"]=="hostname a"
    preview=client.post(f"/api/v1/labs/{lab.id}/import-preview/",exported.content,content_type="application/vnd.containerlab.studio.lab+json")
    assert preview.status_code==200 and preview.data["node_count"]==2 and preview.data["link_count"]==1
    assert preview.data["configured_node_count"]==1 and preview.data["will_replace_draft"] is True
    assert preview.data["deployable"] is False and "does not support startup configuration" in " ".join(preview.data["deployability_issues"])
    assert preview["Cache-Control"]=="no-store" and len(preview.data["checksum"])==64
    imported=client.post(f"/api/v1/labs/{lab.id}/import/",exported.content,content_type="application/vnd.containerlab.studio.lab+json",
        HTTP_IDEMPOTENCY_KEY="bundle-round-trip",HTTP_X_EXPECTED_DRAFT=str(revision.id))
    assert imported.status_code==201, imported.data
    replay=client.post(f"/api/v1/labs/{lab.id}/import/",exported.content,content_type="application/vnd.containerlab.studio.lab+json",
        HTTP_IDEMPOTENCY_KEY="bundle-round-trip",HTTP_X_EXPECTED_DRAFT=str(revision.id))
    assert replay.status_code==200 and replay.data["revision_id"]==imported.data["revision_id"]
    lab.refresh_from_db(); assert lab.current_draft.nodes.count()==2 and lab.current_draft.links.count()==1
    restored=lab.current_draft.nodes.get(name="a")
    assert decrypt_configuration(restored.startup_configuration.encrypted_content)=="hostname a"
    assert AuditEvent.objects.filter(action="lab.exported",target_id=lab.id).exists()
    assert AuditEvent.objects.filter(action="lab.import_previewed",target_id=lab.id).exists()
    assert AuditEvent.objects.filter(action="lab.imported",target_id=lab.id).exists()


@pytest.mark.django_db
def test_viewer_cannot_import_and_missing_project_image_is_rejected():
    owner=User.objects.create_user("bundle-admin",password="long-enough-password")
    viewer=User.objects.create_user("bundle-viewer",password="long-enough-password")
    lab,template,image,revision=_lab(owner,"shared")
    ProjectMembership.objects.create(project=lab.project,user=viewer,role="viewer")
    bundle=export_lab_bundle(lab)
    client=APIClient(); client.force_authenticate(viewer)
    assert client.post(f"/api/v1/labs/{lab.id}/import-preview/",bundle,format="json").status_code==403
    assert client.post(f"/api/v1/labs/{lab.id}/import/",bundle,format="json",HTTP_IDEMPOTENCY_KEY="viewer-import",HTTP_X_EXPECTED_DRAFT=str(revision.id)).status_code==403
    client.force_authenticate(owner)
    rules=template.interface_rules; prefix=rules.get("prefix","eth"); start=int(rules.get("start",1)); count=min(int(rules.get("count",4)),64)
    bundle["topology"]["nodes"]=[{"id":str(uuid.uuid4()),"name":"x","template":{"name":template.template.name,"version":template.version,"kind":template.containerlab_kind},
        "imageDigest":"registry.invalid/device@sha256:"+"f"*64,"position":{},"properties":{},"interfaces":[f"{prefix}{n}" for n in range(start,start+count)],"startupConfiguration":""}]
    response=client.post(f"/api/v1/labs/{lab.id}/import-preview/",bundle,format="json")
    assert response.status_code==422 and "unavailable" in response.data["error"]["details"]

@pytest.mark.django_db
def test_bundle_restore_rejects_draft_changed_after_preview():
    owner=User.objects.create_user("bundle-race",password="long-enough-password");lab,template,image,revision=_lab(owner,"race")
    bundle=export_lab_bundle(lab);client=APIClient();client.force_authenticate(owner)
    preview=client.post(f"/api/v1/labs/{lab.id}/import-preview/",bundle,format="json");assert preview.status_code==200
    replacement=LabRevision.objects.create(lab=lab,revision_number=2,topology_checksum="f"*64);lab.current_draft=replacement;lab.save(update_fields=["current_draft"])
    response=client.post(f"/api/v1/labs/{lab.id}/import/",bundle,format="json",HTTP_IDEMPOTENCY_KEY="bundle-race",
        HTTP_X_EXPECTED_DRAFT=preview.data["expected_current_draft"])
    assert response.status_code==409 and response.data["error"]["code"]=="draft_changed"
    lab.refresh_from_db();assert lab.current_draft_id==replacement.id
