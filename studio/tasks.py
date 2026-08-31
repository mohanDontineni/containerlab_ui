from celery import shared_task
from django.db import transaction
from django.utils import timezone
from .models import OperationJob
from .runtime import ClabernetesAdapter

@shared_task(bind=True,autoretry_for=(ConnectionError,),retry_backoff=True,max_retries=5)
def execute_operation(self,job_id):
    with transaction.atomic():
        job=OperationJob.objects.select_for_update().select_related("deployment__revision").get(pk=job_id)
        if job.state=="succeeded": return str(job.id)
        job.state="started"; job.attempts+=1; job.heartbeat=timezone.now(); job.progress=10; job.save()
    adapter=ClabernetesAdapter()
    try:
        result=getattr(adapter,job.operation_type)(job.deployment)
        job.state="succeeded"; job.progress=100; job.error_details={}
    except Exception as exc:
        job.state="failed"; job.error_details={"type":type(exc).__name__,"message":str(exc)[:2000]}; raise
    finally: job.heartbeat=timezone.now(); job.save()
    return result

