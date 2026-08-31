from celery import shared_task
from django.db import transaction
from django.utils import timezone
from .models import DeviceInstance, LabDeployment, LabNode, OperationJob
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
        if job.operation_type=="ping":
            node=LabNode.objects.get(pk=job.request_payload["node_id"],revision=job.deployment.revision)
            result=adapter.ping(job.deployment,node,job.request_payload["target"],job.request_payload["count"],job.request_payload["timeout"])
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
        if job.operation_type not in ("ping",*device_operations):
            deployment.last_reconciliation=timezone.now()
            deployment.error_details={}
            deployment.save(update_fields=["observed_state","resource_identities","last_reconciliation","error_details","updated_at"])
        job.state="succeeded"; job.progress=100; job.error_details={}
        if job.operation_type=="ping" or job.operation_type in device_operations: job.result_payload=result
    except Exception as exc:
        if job.deployment_id and job.operation_type not in ("ping",*device_operations):
            LabDeployment.objects.filter(pk=job.deployment_id).update(observed_state=LabDeployment.State.FAILED,
                error_details={"type":type(exc).__name__,"message":str(exc)[:2000]},last_reconciliation=timezone.now())
        job.state="failed"; job.error_details={"type":type(exc).__name__,"message":str(exc)[:2000]}; raise
    finally: job.heartbeat=timezone.now(); job.save()
    if job.operation_type=="restart_device": reconcile_deployment.apply_async(args=[str(job.deployment_id)],countdown=3)
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
        for observed_device in ClabernetesAdapter().observe_devices(deployment):
            node=deployment.revision.nodes.filter(name=observed_device["name"]).first()
            if node:
                current=DeviceInstance.objects.filter(deployment=deployment,lab_node=node).first()
                resources={"node_uid":observed_device["node_uid"],"pod":observed_device["pod"],"pod_uid":observed_device["pod_uid"],"pod_phase":observed_device["pod_phase"]}
                same_launcher=current and current.runtime_resources.get("pod_uid")==observed_device["pod_uid"]
                if same_launcher: resources={**current.runtime_resources,**resources}
                DeviceInstance.objects.update_or_create(deployment=deployment,lab_node=node,defaults={
                    "runtime_resources":resources,"observed_readiness":observed_device["readiness"],
                    "worker_placement":observed_device["worker"] or ""})
        deployment.save(update_fields=["observed_state","last_reconciliation","error_details","resource_identities","updated_at"])
        return observed
    except Exception as exc:
        deployment.last_reconciliation=timezone.now()
        deployment.error_details={"type":type(exc).__name__,"message":str(exc)[:2000]}
        deployment.save(update_fields=["last_reconciliation","error_details","updated_at"])
        raise
