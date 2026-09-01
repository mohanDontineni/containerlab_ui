from celery import shared_task
import hashlib
import os
from pathlib import Path
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from .configurations import encrypt_configuration
from .models import AuditEvent, CaptureSession, ConfigurationVersion, ConsoleSession, DeploymentSchedule, DeviceInstance, ImageArtifact, ImageBuild, LabArtifact, LabDeployment, LabLink, LabNode, OperationJob, Project, PublishedImage
from .runtime import ClabernetesAdapter
from .uploads import cleanup_stale_uploads

def publish_platform_health(key,payload):
    try: cache.set(key,payload,120)
    except Exception: pass

@shared_task(bind=True,autoretry_for=(ConnectionError,),retry_backoff=True,max_retries=5)
def execute_operation(self,job_id):
    with transaction.atomic():
        # OperationJob.deployment is nullable, so locking across select_related
        # becomes an unsupported outer-join lock on PostgreSQL.
        job=OperationJob.objects.select_for_update().get(pk=job_id)
        if job.state=="succeeded": return str(job.id)
        job.state="started"; job.attempts+=1; job.heartbeat=timezone.now(); job.progress=10; job.save()
    adapter=ClabernetesAdapter()
    device_operations=("restart_device","reset_device","stop_device","start_device","suspend_device","resume_device","collect_configuration","get_device_logs","inspect_device")
    try:
        if job.operation_type=="publish_image":
            artifact=ImageArtifact.objects.get(pk=job.target_id)
            build=ImageBuild.objects.get(pk=job.request_payload["build_id"],artifact=artifact)
            build.status="running"; build.started_at=timezone.now(); build.save(update_fields=["status","started_at","updated_at"])
            result=adapter.publish_local_image(artifact,build)
            published,_=PublishedImage.objects.update_or_create(artifact=artifact,registry_digest=result["reference"],defaults={"build":build,"repository":result["repository"],"architecture":artifact.architecture,"compatibility_result":{k:v for k,v in result.items() if k!="logs"},"lifecycle_status":"ready"})
            build.status="succeeded"; build.finished_at=timezone.now(); build.log_reference=f"kubernetes-job/{build.job_identity}"; build.log_excerpt=str(result.get("logs", ""))[-12000:]; build.failure_details={}; build.save()
            result={**{k:v for k,v in result.items() if k!="logs"},"published_image_id":str(published.id)}
        elif job.operation_type in ("ping","traceroute"):
            node=LabNode.objects.get(pk=job.request_payload["node_id"],revision=job.deployment.revision)
            if job.operation_type=="ping":
                result=adapter.ping(job.deployment,node,job.request_payload["target"],job.request_payload["count"],job.request_payload["timeout"])
            else:
                result=adapter.traceroute(job.deployment,node,job.request_payload["target"],job.request_payload["max_hops"],
                    job.request_payload["timeout"],job.request_payload["probes"])
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
            if job.operation_type=="get_device_logs": result=adapter.get_device_logs(job.deployment,device,job.request_payload["source"],job.request_payload["tail"])
            elif job.operation_type=="inspect_device": result=adapter.inspect_device(job.deployment,device)
            else: result=getattr(adapter,job.operation_type)(job.deployment,device)
            if job.operation_type in ("restart_device","reset_device","stop_device","start_device","suspend_device","resume_device"):
                device.observed_readiness=result["readiness"]
                resources={**device.runtime_resources,"manual_lifecycle":job.operation_type,"manual_lifecycle_at":timezone.now().isoformat()}
                if job.operation_type=="suspend_device": resources["manual_desired_state"]="suspended"
                elif job.operation_type=="stop_device": resources.update({"manual_desired_state":"stopped","pod":None,"pod_uid":None,
                    "pod_phase":"Stopped","appliance_running":False,"appliance_paused":False})
                elif job.operation_type in ("start_device","resume_device","restart_device","reset_device"): resources.pop("manual_desired_state",None)
                device.runtime_resources=resources
                device.save(update_fields=["observed_readiness","runtime_resources","updated_at"])
                if job.operation_type=="reset_device":
                    ConsoleSession.objects.filter(device=device,revoked_at__isnull=True).update(revoked_at=timezone.now())
            elif job.operation_type=="collect_configuration":
                content=result.pop("content");checksum=hashlib.sha256(content.encode("utf-8")).hexdigest()
                project_id=job.deployment.revision.lab.project_id
                name=f"{job.deployment.revision.lab.name}/{device.lab_node.name}/collected"[:120]
                with transaction.atomic():
                    Project.objects.select_for_update().get(pk=project_id)
                    version=(ConfigurationVersion.objects.filter(project_id=project_id,name=name).aggregate(n=Max("version"))["n"] or 0)+1
                    collected=ConfigurationVersion.objects.create(project_id=project_id,name=name,version=version,
                        encrypted_content=encrypt_configuration(content),checksum=checksum,created_by=job.owner)
                result={**result,"configuration_version_id":str(collected.id),"version":version,"checksum":checksum,
                    "download":f"/api/v1/deployments/{job.deployment_id}/configurations/{collected.id}/download/"}
                AuditEvent.objects.create(actor=job.owner,project_id=project_id,action="configuration.collected",target_type="ConfigurationVersion",
                    target_id=collected.id,correlation_id="",metadata={"deployment":str(job.deployment_id),"device":device.lab_node.name,
                        "version":version,"checksum":checksum,"byte_size":result["byte_size"]})
        else:
            result=getattr(adapter,job.operation_type)(job.deployment)
        deployment=job.deployment
        if job.operation_type in ("deploy_lab","redeploy_lab"):
            deployment.observed_state=LabDeployment.State.DEPLOYING
            deployment.resource_identities={"topology":{"name":"topology","namespace":deployment.namespace},
                "last_redeploy_at":timezone.now().isoformat()} if job.operation_type=="redeploy_lab" else {"topology":{"name":"topology","namespace":deployment.namespace}}
        elif job.operation_type=="stop_lab":
            deployment.observed_state=LabDeployment.State.STOPPED
        elif job.operation_type=="delete_runtime":
            removed_at=timezone.now();deployment.observed_state=LabDeployment.State.REMOVED;deployment.removed_at=removed_at;deployment.requested_desired_state="removed"
            deployment.resource_identities={**deployment.resource_identities,"removal":result}
            deployment.devices.update(observed_readiness="removed",worker_placement="")
            for device in deployment.devices.all():
                device.runtime_resources={**device.runtime_resources,"pod":None,"pod_uid":None,"pod_phase":"Removed",
                    "appliance_running":False,"appliance_paused":False}
                device.save(update_fields=["runtime_resources","updated_at"])
            ConsoleSession.objects.filter(device__deployment=deployment,revoked_at__isnull=True).update(revoked_at=removed_at)
            AuditEvent.objects.create(actor=job.owner,project=deployment.revision.lab.project,action="deployment.removed",
                target_type="LabDeployment",target_id=deployment.id,correlation_id="",metadata={"operation":str(job.id),
                    "namespace":deployment.namespace,"namespace_deleted":bool(result.get("namespaceDeleted")),"revision":deployment.revision.revision_number})
        if job.operation_type not in ("publish_image","ping","capture_packets","set_link_condition",*device_operations):
            deployment.last_reconciliation=timezone.now()
            deployment.error_details={}
            deployment.save(update_fields=["observed_state","requested_desired_state","resource_identities","last_reconciliation","error_details","removed_at","updated_at"])
        job.state="succeeded"; job.progress=100; job.error_details={}
        if job.operation_type in ("publish_image","ping","traceroute","capture_packets","set_link_condition","delete_runtime") or job.operation_type in device_operations: job.result_payload=result
    except Exception as exc:
        if job.operation_type=="publish_image":
            ImageBuild.objects.filter(pk=job.request_payload.get("build_id")).update(status="failed",finished_at=timezone.now(),failure_details={"type":type(exc).__name__,"message":str(exc)[:2000]})
            if job.request_payload.get("force"): PublishedImage.objects.filter(artifact_id=job.target_id,lifecycle_status="reconciling").update(lifecycle_status="failed")
        if job.operation_type=="capture_packets": CaptureSession.objects.filter(pk=job.target_id).update(status="failed")
        if job.deployment_id and job.operation_type not in ("ping","traceroute","capture_packets","set_link_condition",*device_operations):
            LabDeployment.objects.filter(pk=job.deployment_id).update(observed_state=LabDeployment.State.FAILED,
                error_details={"type":type(exc).__name__,"message":str(exc)[:2000]},last_reconciliation=timezone.now())
        job.state="failed"; job.error_details={"type":type(exc).__name__,"message":str(exc)[:2000]}; raise
    finally: job.heartbeat=timezone.now(); job.save()
    if job.operation_type in ("restart_device","reset_device","start_device"):
        for countdown in (3,10,30): reconcile_deployment.apply_async(args=[str(job.deployment_id)],countdown=countdown)
    return result

@shared_task(bind=True,autoretry_for=(ConnectionError,),retry_backoff=True,max_retries=5)
def execute_staged_start(self,job_id):
    try:
        with transaction.atomic():
            # OperationJob.deployment is nullable; PostgreSQL rejects a row lock
            # that traverses that nullable outer join. Lock the job alone and
            # load its protected deployment relation separately when accessed.
            job=OperationJob.objects.select_for_update().get(pk=job_id)
            if job.operation_type!="staged_start_devices":raise RuntimeError("Operation is not a staged start")
            if job.state in ("succeeded","failed"):return job.result_payload
            ordered_ids=job.request_payload["device_ids"];started=list(job.result_payload.get("devices",[]));index=len(started)
            if index>=len(ordered_ids):return job.result_payload
            device=DeviceInstance.objects.select_for_update().select_related("lab_node").get(pk=ordered_ids[index],deployment=job.deployment)
            if device.runtime_resources.get("manual_desired_state")!="stopped":raise RuntimeError(f"{device.lab_node.name} is no longer stopped")
            job.state="started";job.attempts+=1;job.heartbeat=timezone.now();job.progress=max(10,job.progress);job.save(
                update_fields=["state","attempts","heartbeat","progress","updated_at"])
        step=ClabernetesAdapter().start_device(job.deployment,device)
        with transaction.atomic():
            job=OperationJob.objects.select_for_update().get(pk=job_id)
            device=DeviceInstance.objects.select_for_update().select_related("lab_node").get(pk=ordered_ids[index],deployment=job.deployment)
            device.observed_readiness=step["readiness"]
            resources={**device.runtime_resources,"manual_lifecycle":"staged_start","manual_lifecycle_at":timezone.now().isoformat()}
            resources.pop("manual_desired_state",None);device.runtime_resources=resources
            device.save(update_fields=["observed_readiness","runtime_resources","updated_at"])
            started=list(job.result_payload.get("devices",[]));started.append({"device_id":str(device.id),"device":device.lab_node.name,
                "position":index+1,"started_at":timezone.now().isoformat()})
            result={"devices":started,"interval_seconds":job.request_payload["interval_seconds"],"count":len(started)}
            job.result_payload=result;job.heartbeat=timezone.now();job.progress=min(100,10+round(90*len(started)/len(ordered_ids)))
            if len(started)==len(ordered_ids):
                job.state="succeeded";job.progress=100;job.error_details={}
                AuditEvent.objects.create(actor=job.owner,project=job.deployment.revision.lab.project,action="device.staged_start_completed",
                    target_type="LabDeployment",target_id=job.deployment_id,correlation_id=str(job.id),
                    metadata={"operation":str(job.id),"devices":[row["device"] for row in started],"interval_seconds":job.request_payload["interval_seconds"]})
                transaction.on_commit(lambda:[reconcile_deployment.apply_async(args=[str(job.deployment_id)],countdown=countdown) for countdown in (3,10,30)])
            else:
                interval=job.request_payload["interval_seconds"]
                transaction.on_commit(lambda:execute_staged_start.apply_async(args=[str(job.id)],countdown=interval))
            job.save(update_fields=["state","progress","heartbeat","result_payload","error_details","updated_at"])
        return result
    except Exception as exc:
        OperationJob.objects.filter(pk=job_id).update(state="failed",heartbeat=timezone.now(),error_details={"type":type(exc).__name__,"message":str(exc)[:2000]})
        raise

@shared_task(bind=True,autoretry_for=(ConnectionError,),retry_backoff=True,max_retries=5)
def reconcile_deployment(self,deployment_id):
    deployment=LabDeployment.objects.get(pk=deployment_id)
    if deployment.removed_at: return LabDeployment.State.REMOVED
    try:
        adapter=ClabernetesAdapter();status=adapter.get_observed_state(deployment)
        state=status.get("topologyState","").lower()
        if status.get("topologyReady") is True: observed=LabDeployment.State.RUNNING
        elif state in ("failed","error"): observed=LabDeployment.State.FAILED
        elif state in ("stopped","deleted"): observed=LabDeployment.State.STOPPED
        else: observed=LabDeployment.State.DEPLOYING
        deployment.observed_state=observed
        deployment.last_reconciliation=timezone.now()
        deployment.error_details={} if observed!=LabDeployment.State.FAILED else {"runtime_status":status}
        deployment.resource_identities={**deployment.resource_identities,"status":status}
        observed_devices=adapter.observe_devices(deployment)
        telemetry_available=any(item.get("telemetry") for item in observed_devices)
        telemetry_reason=next((item.get("telemetry_error") for item in observed_devices if item.get("telemetry_error")),None)
        publish_platform_health("studio:platform:metrics",{"available":telemetry_available,"reason":telemetry_reason,
            "checked_at":timezone.now().isoformat()})
        publish_platform_health("studio:platform:runtime",{"available":True,"version":deployment.runtime_version,
            "checked_at":timezone.now().isoformat()})
        for observed_device in observed_devices:
            node=deployment.revision.nodes.filter(name=observed_device["name"]).first()
            if node:
                current=DeviceInstance.objects.filter(deployment=deployment,lab_node=node).first()
                desired_suspended=current and current.runtime_resources.get("manual_desired_state")=="suspended"
                desired_stopped=current and current.runtime_resources.get("manual_desired_state")=="stopped"
                resources={"node_uid":observed_device["node_uid"],"pod":observed_device["pod"],"pod_uid":observed_device["pod_uid"],"pod_phase":observed_device["pod_phase"],"appliance_running":observed_device["appliance_running"],"appliance_paused":observed_device["appliance_paused"]}
                resources["telemetry"]={"available":True,**observed_device["telemetry"]} if observed_device.get("telemetry") else {"available":False,"reason":observed_device.get("telemetry_error") or ("compute_released" if not observed_device.get("pod") else "metrics_pending")}
                same_launcher=current and current.runtime_resources.get("pod_uid")==observed_device["pod_uid"]
                if same_launcher: resources={**current.runtime_resources,**resources}
                if desired_suspended or desired_stopped:
                    resources["manual_desired_state"]="suspended" if desired_suspended else "stopped"
                    if current.runtime_resources.get("manual_lifecycle"): resources["manual_lifecycle"]=current.runtime_resources["manual_lifecycle"]
                    if current.runtime_resources.get("manual_lifecycle_at"): resources["manual_lifecycle_at"]=current.runtime_resources["manual_lifecycle_at"]
                linked_interfaces=adapter.linked_data_interfaces(node) if desired_suspended else []
                if desired_stopped:
                    if observed_device.get("pod") or not observed_device.get("deployment_disabled"):
                        adapter.ensure_device_stopped(deployment,current)
                    resources.update({"pod":None,"pod_uid":None,"pod_phase":"Stopped","appliance_running":False,"appliance_paused":False})
                elif desired_suspended and observed_device["appliance_running"] and not observed_device["appliance_paused"]:
                    adapter.set_device_pause(deployment,node.name,observed_device["pod"],True,linked_interfaces)
                    resources.update({"appliance_running":False,"appliance_paused":True})
                elif desired_suspended and observed_device["appliance_paused"]:
                    adapter.set_device_links(deployment,node.name,observed_device["pod"],linked_interfaces,False)
                readiness="stopped" if desired_stopped else "suspended" if desired_suspended and resources["appliance_paused"] else observed_device["readiness"]
                DeviceInstance.objects.update_or_create(deployment=deployment,lab_node=node,defaults={
                    "runtime_resources":resources,"observed_readiness":readiness,
                    "worker_placement":observed_device["worker"] or ""})
        waiting=[item["name"] for item in observed_devices if item["readiness"]!="ready" and not DeviceInstance.objects.filter(
            deployment=deployment,lab_node__name=item["name"],observed_readiness__in=("suspended","stopped"),
            runtime_resources__manual_desired_state__in=("suspended","stopped")).exists()]
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

@shared_task
def reconcile_active_deployments():
    active=(LabDeployment.State.PENDING,LabDeployment.State.DEPLOYING,LabDeployment.State.RUNNING,LabDeployment.State.DEGRADED)
    deployment_ids=list(LabDeployment.objects.filter(observed_state__in=active).values_list("id",flat=True))
    for deployment_id in deployment_ids: reconcile_deployment.delay(str(deployment_id))
    return len(deployment_ids)

@shared_task
def expire_stale_uploads(): return cleanup_stale_uploads()

@shared_task
def dispatch_due_schedules():
    dispatched=0
    for schedule_id in list(DeploymentSchedule.objects.filter(status=DeploymentSchedule.Status.PENDING,execute_at__lte=timezone.now()).order_by("execute_at","created_at","id").values_list("id",flat=True)[:100]):
        with transaction.atomic():
            schedule=DeploymentSchedule.objects.select_for_update().select_related("deployment__revision__lab__project","created_by").get(pk=schedule_id)
            if schedule.status!=DeploymentSchedule.Status.PENDING: continue
            deployment=schedule.deployment;reason=None
            if deployment.removed_at: reason="runtime_removed"
            elif deployment.operations.filter(state__in=("accepted","scheduled","started")).exists(): reason="operation_in_progress"
            elif schedule.action==DeploymentSchedule.Action.START and deployment.observed_state not in (LabDeployment.State.STOPPED,LabDeployment.State.FAILED): reason=f"state_{deployment.observed_state}"
            elif schedule.action==DeploymentSchedule.Action.STOP and deployment.observed_state not in (LabDeployment.State.RUNNING,LabDeployment.State.DEGRADED,LabDeployment.State.DEPLOYING): reason=f"state_{deployment.observed_state}"
            if reason:
                schedule.status=DeploymentSchedule.Status.SKIPPED;schedule.save(update_fields=["status","updated_at"])
                AuditEvent.objects.create(actor=schedule.created_by,project=deployment.revision.lab.project,action="deployment.schedule_skipped",target_type="DeploymentSchedule",target_id=schedule.id,correlation_id=str(schedule.id),metadata={"action":schedule.action,"reason":reason,"execute_at":schedule.execute_at.isoformat()})
                continue
            operation_type="deploy_lab" if schedule.action==DeploymentSchedule.Action.START else "stop_lab"
            job=OperationJob.objects.create(deployment=deployment,owner=schedule.created_by,operation_type=operation_type,target_id=deployment.id,idempotency_key=f"deployment-schedule:{schedule.id}",state="scheduled",request_payload={"deployment_schedule":str(schedule.id)})
            schedule.operation=job;schedule.status=DeploymentSchedule.Status.DISPATCHED;schedule.save(update_fields=["operation","status","updated_at"])
            AuditEvent.objects.create(actor=schedule.created_by,project=deployment.revision.lab.project,action="deployment.schedule_dispatched",target_type="DeploymentSchedule",target_id=schedule.id,correlation_id=str(schedule.id),metadata={"action":schedule.action,"operation":str(job.id),"execute_at":schedule.execute_at.isoformat()})
            transaction.on_commit(lambda job_id=str(job.id):execute_operation.delay(job_id));dispatched+=1
    return dispatched
