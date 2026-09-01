from rest_framework.routers import DefaultRouter
from .api import ProjectViewSet,MembershipViewSet,LabViewSet,UploadViewSet,ImageArtifactViewSet,ImageCredentialReferenceViewSet,DeploymentViewSet,DeviceTemplateViewSet
router=DefaultRouter(); router.register("projects",ProjectViewSet,"project"); router.register("memberships",MembershipViewSet,"membership"); router.register("labs",LabViewSet,"lab"); router.register("uploads",UploadViewSet,"upload"); router.register("images",ImageArtifactViewSet,"image"); router.register("image-credentials",ImageCredentialReferenceViewSet,"image-credential"); router.register("deployments",DeploymentViewSet,"deployment"); router.register("device-templates",DeviceTemplateViewSet,"device-template")
urlpatterns=router.urls
