import uuid
from django.shortcuts import redirect
class CorrelationIdMiddleware:
    def __init__(self, get_response): self.get_response = get_response
    def __call__(self, request):
        request.correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))[:128]
        response = self.get_response(request)
        response["X-Correlation-ID"] = request.correlation_id
        return response

class ForcedPasswordChangeMiddleware:
    allowed_prefixes=("/settings/","/accounts/logout/","/static/")
    def __init__(self,get_response): self.get_response=get_response
    def __call__(self,request):
        if request.user.is_authenticated and request.user.must_change_password and not request.path.startswith(self.allowed_prefixes):
            return redirect("portal-settings")
        return self.get_response(request)
