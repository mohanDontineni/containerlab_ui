from rest_framework.routers import DefaultRouter
from .api import ProjectViewSet,LabViewSet,UploadViewSet,ImageArtifactViewSet,DeploymentViewSet
router=DefaultRouter(); router.register("projects",ProjectViewSet,"project"); router.register("labs",LabViewSet,"lab"); router.register("uploads",UploadViewSet,"upload"); router.register("images",ImageArtifactViewSet,"image"); router.register("deployments",DeploymentViewSet,"deployment")
urlpatterns=router.urls
