from django import forms
from django.db.models import Q
from .models import Lab, Project

class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ("name", "description", "tags")
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Core Network Engineering"}),
            "description": forms.Textarea(attrs={"rows": 4, "placeholder": "What will this project be used for?"}),
            "tags": forms.TextInput(attrs={"placeholder": '["routing", "training"]'}),
        }

class LabForm(forms.ModelForm):
    class Meta:
        model = Lab
        fields = ("project", "name", "description", "tags")
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. BGP Edge Validation"}),
            "description": forms.Textarea(attrs={"rows": 4, "placeholder": "Describe the lab objective and expected outcome"}),
            "tags": forms.TextInput(attrs={"placeholder": '["bgp", "edge"]'}),
        }
    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["project"].queryset = Project.objects.filter(Q(owner=user) | Q(memberships__user=user, memberships__role__in=("administrator", "editor"))).distinct()

