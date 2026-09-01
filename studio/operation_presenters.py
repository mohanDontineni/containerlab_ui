import re

OPERATION_REMEDIATION={
    "publish_image":("Review image","/images/","Inspect validation and publication evidence before scheduling another publication."),
    "prepare_image":("Review image","/images/","Inspect the retained build output and correct the image source or recipe."),
    "import_lab":("Review labs","/labs/","Review the imported lab data and resolve any validation errors."),
    "restore_lab":("Review labs","/labs/","Review the backup compatibility and destination project before restoring again."),
    "restore_revision":("Review labs","/labs/","Open the lab workspace and confirm the active draft and revision history."),
    "delete_lab_record":("Review labs","/labs/","Review the lab library and any remaining protected runtime references."),
    "retire_project":("Review projects","/projects/","Review project ownership, members, labs, images, and active operations."),
    "delete_image":("Review images","/images/","Review image references and retained supply-chain evidence."),
}
SENSITIVE_VALUE=re.compile(r"(?i)\b(authorization|token|password|secret|credential)(\s*[:=]\s*)([^\s,;}{]+)")

def safe_text(value,limit=500,default="Unavailable"):
    text=str(value or default).replace("\x00","")
    return SENSITIVE_VALUE.sub(lambda match:f"{match.group(1)}{match.group(2)}[redacted]",text)[:limit]

def remediation(job):
    if job.deployment_id:
        return {"action_label":"Open deployment","action_url":f"/deployments/{job.deployment_id}/",
            "guidance":"Inspect runtime state and device evidence before repeating the operation.",
            "target_label":job.deployment.revision.lab.name}
    label,url,guidance=OPERATION_REMEDIATION.get(job.operation_type,
        ("Review job","/operations/","Inspect the operation evidence before taking corrective action."))
    if job.operation_type in ("create_device_template","version_device_template") or job.operation_type.startswith("template_"):
        label,url,guidance="Review templates","/device-templates/","Review the launch profile and publish a corrected template version."
    return {"action_label":label,"action_url":url,"guidance":guidance,"target_label":"Platform resource"}

def present(job):
    details=job.error_details if isinstance(job.error_details,dict) else {}
    return {"job":job,"error_type":safe_text(details.get("type"),80,"Operation failed"),
        "error_message":safe_text(details.get("message"),500,"No additional failure detail was reported."),**remediation(job)}
