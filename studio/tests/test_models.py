import pytest
from django.db import IntegrityError,transaction
from studio.models import User,Project,ProjectMembership,OperationJob

@pytest.mark.django_db
def test_membership_is_unique():
    owner=User.objects.create_user("owner",password="long-enough-password")
    member=User.objects.create_user("member",password="long-enough-password")
    p=Project.objects.create(owner=owner,name="p")
    ProjectMembership.objects.create(project=p,user=member,role="viewer")
    with pytest.raises(IntegrityError),transaction.atomic(): ProjectMembership.objects.create(project=p,user=member,role="editor")

@pytest.mark.django_db
def test_idempotency_key_is_unique_per_user():
    user=User.objects.create_user("u",password="long-enough-password")
    OperationJob.objects.create(owner=user,operation_type="deploy_lab",target_id=user.id,idempotency_key="same")
    with pytest.raises(IntegrityError),transaction.atomic(): OperationJob.objects.create(owner=user,operation_type="stop_lab",target_id=user.id,idempotency_key="same")

