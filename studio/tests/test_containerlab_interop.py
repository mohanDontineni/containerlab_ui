import json

import pytest
import yaml
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from studio.models import (AuditEvent, DeviceTemplate, DeviceTemplateVersion, ImageArtifact, Lab, LabDeployment, LabRevision,
                           OperationJob, Project, PublishedImage, User)


def topology_file(payload,name="import.clab.yml"):
    return SimpleUploadedFile(name,payload,content_type="application/yaml")


@pytest.fixture
def interop_catalog(db):
    owner=User.objects.create_user("interop-owner",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="interop-project")
    lab=Lab.objects.create(project=project,name="interop-destination")
    template=DeviceTemplate.objects.create(name="Interop Linux")
    version=DeviceTemplateVersion.objects.create(template=template,version=1,containerlab_kind="linux",
        launch_profile={"verified":True},interface_rules={"prefix":"eth","start":1,"count":2},
        resource_requirements={"cpu":"250m","memory":"256Mi"},capabilities={"verified":True,"console":True,"capture":True})
    template.active_version=version;template.save(update_fields=["active_version"])
    artifact=ImageArtifact.objects.create(project=project,owner=owner,source_type="registry",original_filename="alpine",
        detected_format="oci-registry",byte_size=0,checksum="a"*64,architecture="amd64",storage_reference="docker.io/alpine",
        validation_status="validated")
    image=PublishedImage.objects.create(artifact=artifact,registry_digest="docker.io/alpine@sha256:"+"a"*64,
        repository="docker.io/alpine",architecture="amd64",lifecycle_status="ready")
    payload=("name: imported-edge\ntopology:\n  nodes:\n"
        f"    r1: {{kind: linux, image: '{image.registry_digest}', startup-config: r1.cfg}}\n"
        f"    r2: {{kind: linux, image: '{image.registry_digest}'}}\n"
        "  links:\n    - endpoints: ['r1:eth1', 'r2:eth1']\n").encode()
    return owner,project,lab,version,image,payload


@pytest.mark.django_db
def test_containerlab_gui_preview_import_and_export_are_mapped_guarded_and_audited(interop_catalog):
    owner,project,lab,template,image,payload=interop_catalog
    client=APIClient();client.force_authenticate(owner)
    preview=client.post(f"/api/v1/labs/{lab.id}/containerlab-import-preview/",{"file":topology_file(payload)},format="multipart")
    assert preview.status_code==200,preview.data
    assert preview.data["node_count"]==2 and preview.data["link_count"]==1 and preview.data["external_configuration_count"]==1
    assert preview.data["structurally_importable"] is True and preview.data["expected_current_draft"] is None
    choice=next(item for item in preview.data["nodes"][0]["template_choices"] if item["id"]==str(template.id))
    assert any(item["id"]==str(image.id) and item["source_match"] and item["selectable"] for item in choice["images"])
    mappings={node["name"]:{"template_id":str(template.id),"image_id":str(image.id)} for node in preview.data["nodes"]}
    form={"file":topology_file(payload),"expected_checksum":preview.data["checksum"],"expected_current_draft":"",
        "mappings":json.dumps(mappings),"acknowledge_external_configurations":"false"}
    blocked=client.post(f"/api/v1/labs/{lab.id}/containerlab-import/",form,format="multipart",HTTP_IDEMPOTENCY_KEY="interop-no-ack")
    assert blocked.status_code==422 and "external startup-file" in blocked.data["error"]["details"]
    form["file"]=topology_file(payload);form["acknowledge_external_configurations"]="true"
    imported=client.post(f"/api/v1/labs/{lab.id}/containerlab-import/",form,format="multipart",HTTP_IDEMPOTENCY_KEY="interop-import")
    assert imported.status_code==201,imported.data
    revision=LabRevision.objects.get(pk=imported.data["revision_id"]);lab.refresh_from_db()
    assert lab.current_draft_id==revision.id and revision.nodes.count()==2 and revision.links.count()==1
    assert not revision.nodes.filter(startup_configuration__isnull=False).exists()
    assert revision.nodes.get(name="r1").properties["containerlabImport"]["sourceKind"]=="linux"
    operation=OperationJob.objects.get(id=imported.data["operation_id"])
    assert operation.state=="succeeded" and operation.request_payload.keys()=={"lab_id","topology_checksum"}
    event=AuditEvent.objects.get(action="lab.containerlab_imported")
    assert event.metadata["external_configurations_omitted"]==1
    replay=client.post(f"/api/v1/labs/{lab.id}/containerlab-import/",{},format="multipart",HTTP_IDEMPOTENCY_KEY="interop-import")
    assert replay.status_code==200 and replay.data==imported.data
    exported=client.get(f"/api/v1/labs/{lab.id}/containerlab-export/")
    assert exported.status_code==200 and exported["Content-Type"].startswith("application/yaml") and ".clab.yml" in exported["Content-Disposition"]
    document=yaml.safe_load(exported.content)
    assert set(document["topology"]["nodes"])=={"r1","r2"} and document["topology"]["links"]==[{"endpoints":["r1:eth1","r2:eth1"]}]


@pytest.mark.django_db
def test_containerlab_import_rejects_unsafe_fields_stale_drafts_and_incomplete_mapping(interop_catalog):
    owner,project,lab,template,image,payload=interop_catalog
    client=APIClient();client.force_authenticate(owner)
    unsafe=b"name: unsafe\ntopology:\n  nodes:\n    r1: {kind: linux, image: alpine, binds: ['/:/host']}\n"
    denied=client.post(f"/api/v1/labs/{lab.id}/containerlab-import-preview/",{"file":topology_file(unsafe)},format="multipart")
    assert denied.status_code==422 and "Unsupported fields" in denied.data["error"]["details"]
    preview=client.post(f"/api/v1/labs/{lab.id}/containerlab-import-preview/",{"file":topology_file(payload)},format="multipart")
    draft=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="b"*64);lab.current_draft=draft;lab.save(update_fields=["current_draft"])
    mappings={"r1":{"template_id":str(template.id),"image_id":str(image.id)}}
    stale=client.post(f"/api/v1/labs/{lab.id}/containerlab-import/",{"file":topology_file(payload),"expected_checksum":preview.data["checksum"],
        "expected_current_draft":"","mappings":json.dumps(mappings),"acknowledge_external_configurations":"true"},format="multipart",HTTP_IDEMPOTENCY_KEY="stale-interop")
    assert stale.status_code==409 and stale.data["error"]["code"]=="draft_changed"
    assert not OperationJob.objects.filter(idempotency_key="stale-interop").exists()
    incomplete=client.post(f"/api/v1/labs/{lab.id}/containerlab-import/",{"file":topology_file(payload),"expected_checksum":preview.data["checksum"],
        "expected_current_draft":str(draft.id),"mappings":json.dumps(mappings),"acknowledge_external_configurations":"true"},format="multipart",HTTP_IDEMPOTENCY_KEY="incomplete-interop")
    assert incomplete.status_code==422 and "Map every imported device" in incomplete.data["error"]["details"]
    lab.refresh_from_db();assert lab.current_draft_id==draft.id and not OperationJob.objects.filter(idempotency_key="incomplete-interop").exists()
