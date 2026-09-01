import re

OCI_DIGEST=re.compile(r"@sha256:[0-9a-f]{64}$")

def immutable_publication(image):
    reference=image.registry_digest or ""
    if OCI_DIGEST.search(reference): return True
    result=image.compatibility_result if isinstance(image.compatibility_result,dict) else {}
    expected=f":sha256-{image.artifact.checksum}"
    return result.get("publication_mode")=="node-containerd" and reference.endswith(expected)

def evaluate(template_version,image):
    configured=getattr(template_version,"image_requirements",{})
    requirements=configured if isinstance(configured,dict) else {}
    artifact=image.artifact
    reasons=[];warnings=[]
    if getattr(artifact,"deleted_at",None): reasons.append("The source image artifact has been deleted.")
    if getattr(artifact,"validation_status","validated")!="validated": reasons.append("The source artifact has not passed validation.")
    lifecycle_status=getattr(image,"lifecycle_status","ready")
    if lifecycle_status not in ("ready","verified","unverified"): reasons.append(f"Publication state is {lifecycle_status}.")
    if requirements.get("digest_required_for_deploy",True) and not immutable_publication(image): reasons.append("image is not content-addressed")
    architectures=requirements.get("architectures") or []
    architecture=getattr(image,"architecture","")
    if architectures and architecture not in architectures: reasons.append(f"Architecture {architecture or 'unspecified'} is not allowed; expected {', '.join(architectures)}.")
    formats=requirements.get("formats") or []
    detected_format=getattr(artifact,"detected_format","")
    if formats and detected_format not in formats: reasons.append(f"Format {detected_format or 'unspecified'} is not allowed.")
    categories=[str(value).lower() for value in requirements.get("categories") or []]
    category=getattr(artifact,"category","")
    if categories and category.lower() not in categories: reasons.append(f"Image category {category or 'unclassified'} does not match this template.")
    if requirements.get("verified_publication_required") and lifecycle_status!="verified": reasons.append("This template requires a verified publication.")
    elif lifecycle_status=="unverified": warnings.append("Runtime compatibility has not been verified.")
    result=image.compatibility_result if isinstance(image.compatibility_result,dict) else {}
    if result.get("runtime_pull")=="not_yet_verified": warnings.append("Runtime-layer authentication and pull remain unverified.")
    status="incompatible" if reasons else "warning" if warnings else "compatible"
    return {"status":status,"selectable":not reasons,"reasons":reasons,"warnings":warnings,
        "requirements":{"architectures":architectures,"formats":formats,"categories":categories,
            "verified_publication_required":bool(requirements.get("verified_publication_required")),
            "digest_required":bool(requirements.get("digest_required_for_deploy",True))}}
