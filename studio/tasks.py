from celery import shared_task
import hashlib
import os
from pathlib import Path
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import CaptureSession, DeviceInstance, ImageArtifact, ImageBuild, LabArtifact, LabDeployment, LabLink, LabNode, OperationJob, PublishedImage
from .runtime import ClabernetesAdapter

@shared_task(bind=True,autoretry_for=(ConnectionError,),retry_backoff=True,max_retries=5)
def execute_operation(self,job_id):
    with transaction.atomic():
        # OperationJob.deployment is nullable, so locking across select_related
        # becomes an unsupported outer-join lock on PostgreSQL.
        job=OperationJob.objects.select_for_update().get(pk=job_id)
        if job.state=="succeeded": return str(job.id)
        job.state="started"; job.attempts+=1; job.heartbeat=timezone.now(); job.progress=10; job.save()
    adapter=ClabernetesAdapter()
    device_operations=("restart_device",)
    try:
        if job.operation_type=="publish_image":
            artifact=ImageArtifact.objects.get(pk=job.target_id)
            build=ImageBuild.objects.get(pk=job.request_payload["build_id"],artifact=artifact)
            build.status="running"; build.started_at=timezone.now(); build.save(update_fields=["status","started_at","updated_at"])
            result=adapter.publish_local_image(artifact,build)
            published,_=PublishedImage.objects.update_or_create(artifact=artifact,registry_digest=result["reference"],defaults={"build":build,"repository":result["repository"],"architecture":artifact.architecture,"compatibility_result":{k:v for k,v in result.items() if k!="logs"},"lifecycle_status":"ready"})
            build.status="succeeded"; build.finished_at=timezone.now(); build.log_reference=f"kubernetes-job/{build.job_identity}"; build.failure_details={}; build.save()
            result={**{k:v for k,v in result.items() if k!="logs"},"published_image_id":str(published.id)}
        elif job.operation_type=="ping":
            node=LabNode.objects.get(pk=job.request_payload["node_id"],revision=job.deployment.revision)
            result=adapter.ping(job.deployment,node,job.request_payload["target"],job.request_payload["count"],job.request_payload["timeout"])
        elif job.operation_type=="capture_packets":
            capture=CaptureSession.objects.select_related("interface__node").get(pk=job.target_id,deployment=job.deployment)
            capture.status="capturing"; capture.save(update_fields=["status","updated_at"])
            payload=adapter.capture_packets(job.deployment,capture.interface.node,capture.interface,
                job.request_payload["duration"],job.request_payload["packet_limit"])
            checksum=hashlib.sha256(payload).hexdigest(); directory=Path(settings.MEDIA_ROOT)/"captures"/str(job.deployment_id)
            directory.mkdir(parents=True,exist_ok=True); destination=directory/f"{capture.id}.pcap"; temporary=destination.with_suffix(".tmp")
            temporary.write_bytes(payload); os.replace(temporary,destination)
            artifact=LabArtifact.objects.create(deployment=job.deployment,artifact_type="packet_capture",storage_reference=str(destination),
                checksum=checksum,retention_until=capture.expires_at)
            capture.status="complete"; capture.artifact_reference=str(destination); capture.save(update_fields=["status","artifact_reference","updated_at"])
            result={"capture_id":str(capture.id),"artifact_id":str(artifact.id),"byte_size":len(payload),"checksum":checksum,
                "download":f"/api/v1/deployments/{job.deployment_id}/captures/{capture.id}/download/"}
        elif job.operation_type=="set_link_condition":
            link=LabLink.objects.select_related("endpoint_a__node","endpoint_b__node").get(pk=job.target_id,revision=job.deployment.revision)
            condition=job.request_payload["condition"]
            result=adapter.set_link_condition(job.deployment,link,condition)
            deployment=job.deployment
            conditions=dict(deployment.resource_identities.get("link_conditions",{}))
            if condition.get("active"): conditions[str(link.id)]=condition
            else: conditions.pop(str(link.id),None)
            deployment.resource_identities={**deployment.resource_identities,"link_conditions":conditions}
            deployment.save(update_fields=["resource_identities","updated_at"])
        elif job.operation_type in device_operations:
            device=DeviceInstance.objects.select_related("lab_node").get(pk=job.target_id,deployment=job.deployment)
            result=getattr(adapter,job.operation_type)(job.deployment,device)
            device.observed_readiness=result["readiness"]
            device.runtime_resources={**device.runtime_resources,"manual_lifecycle":job.operation_type,"manual_lifecycle_at":timezone.now().isoformat()}
            device.save(update_fields=["observed_readiness","runtime_resources","updated_at"])
        else:
            result=getattr(adapter,job.operation_type)(job.deployment)
        deployment=job.deployment
        if job.operation_type=="deploy_lab":
            deployment.observed_state=LabDeployment.State.DEPLOYING
            deployment.resource_identities={"topology":{"name":"topology","namespace":deployment.namespace}}
        elif job.operation_type in ("stop_lab","delete_runtime"):
            deployment.observed_state=LabDeployment.State.STOPPED
        if job.operation_type not in ("publish_image","ping","capture_packets","set_link_condition",*device_operations):
            deployment.last_reconciliation=timezone.now()
            deployment.error_details={}
            deployment.save(update_fields=["observed_state","resource_identities","last_reconciliation","error_details","updated_at"])
        job.state="succeeded"; job.progress=100; job.error_details={}
        if job.operation_type in ("publish_image","ping","capture_packets","set_link_condition") or job.operation_type in device_operations: job.result_payload=result
    except Exception as exc:
        if job.operation_type=="publish_image":
            ImageBuild.objects.filter(pk=job.request_payload.get("build_id")).update(status="failed",finished_at=timezone.now(),failure_details={"type":type(exc).__name__,"message":str(exc)[:2000]})
            if job.request_payload.get("force"): PublishedImage.objects.filter(artifact_id=job.target_id,lifecycle_status="reconciling").update(lifecycle_status="failed")
        if job.operation_type=="capture_packets": CaptureSession.objects.filter(pk=job.target_id).update(status="failed")
        if job.deployment_id and job.operation_type not in ("ping","capture_packets","set_link_condition",*device_operations):
            LabDeployment.objects.filter(pk=job.deployment_id).update(observed_state=LabDeployment.State.FAILED,
                error_details={"type":type(exc).__name__,"message":str(exc)[:2000]},last_reconciliation=timezone.now())
        job.state="failed"; job.error_details={"type":type(exc).__name__,"message":str(exc)[:2000]}; raise
    finally: job.heartbeat=timezone.now(); job.save()
    if job.operation_type=="restart_device":
        for countdown in (3,10,30): reconcile_deployment.apply_async(args=[str(job.deployment_id)],countdown=countdown)
    return result

@shared_task(bind=True,autoretry_for=(ConnectionError,),retry_backoff=True,max_retries=5)
def reconcile_deployment(self,deployment_id):
    deployment=LabDeployment.objects.get(pk=deployment_id)
    try:
        status=ClabernetesAdapter().get_observed_state(deployment)
        state=status.get("topologyState","").lower()
        if status.get("topologyReady") is True: observed=LabDeployment.State.RUNNING
        elif state in ("failed","error"): observed=LabDeployment.State.FAILED
        elif state in ("stopped","deleted"): observed=LabDeployment.State.STOPPED
        else: observed=LabDeployment.State.DEPLOYING
        deployment.observed_state=observed
        deployment.last_reconciliation=timezone.now()
        deployment.error_details={} if observed!=LabDeployment.State.FAILED else {"runtime_status":status}
        deployment.resource_identities={**deployment.resource_identities,"status":status}
        observed_devices=ClabernetesAdapter().observe_devices(deployment)
        for observed_device in observed_devices:
            node=deployment.revision.nodes.filter(name=observed_device["name"]).first()
            if node:
                current=DeviceInstance.objects.filter(deployment=deployment,lab_node=node).first()
                resources={"node_uid":observed_device["node_uid"],"pod":observed_device["pod"],"pod_uid":observed_device["pod_uid"],"pod_phase":observed_device["pod_phase"],"appliance_running":observed_device["appliance_running"]}
                same_launcher=current and current.runtime_resources.get("pod_uid")==observed_device["pod_uid"]
                if same_launcher: resources={**current.runtime_resources,**resources}
                DeviceInstance.objects.update_or_create(deployment=deployment,lab_node=node,defaults={
                    "runtime_resources":resources,"observed_readiness":observed_device["readiness"],
                    "worker_placement":observed_device["worker"] or ""})
        waiting=[item["name"] for item in observed_devices if item["readiness"]!="ready"]
        if observed==LabDeployment.State.RUNNING and waiting:
            deployment.observed_state=observed=LabDeployment.State.DEPLOYING
            deployment.error_details={"waiting_for_devices":waiting}
        deployment.save(update_fields=["observed_state","last_reconciliation","error_details","resource_identities","updated_at"])
        return observed
    except Exception as exc:
        deployment.last_reconciliation=timezone.now()
        deployment.error_details={"type":type(exc).__name__,"message":str(exc)[:2000]}
        deployment.save(update_fields=["last_reconciliation","error_details","updated_at"])
        raise
