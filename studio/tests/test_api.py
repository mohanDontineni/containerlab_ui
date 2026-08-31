import pytest
from rest_framework.test import APIClient
from studio.models import User,Project,ProjectMembership

@pytest.mark.django_db
def test_guessed_project_uuid_is_not_visible():
    owner=User.objects.create_user("owner",password="long-enough-password")
    attacker=User.objects.create_user("attacker",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="secret")
    c=APIClient(); c.force_authenticate(attacker)
    assert c.get(f"/api/v1/projects/{project.id}/").status_code==404
@pytest.mark.django_db
def test_viewer_cannot_modify_project():
    owner=User.objects.create_user("owner",password="long-enough-password")
    viewer=User.objects.create_user("viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="p")
    ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    c=APIClient(); c.force_authenticate(viewer)
    assert c.patch(f"/api/v1/projects/{project.id}/",{"name":"changed"},format="json").status_code==403

