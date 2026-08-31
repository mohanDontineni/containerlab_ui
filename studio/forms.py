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

class RegistryImageForm(forms.Form):
    project = forms.ModelChoiceField(queryset=Project.objects.none())
    name = forms.CharField(max_length=120, help_text="A recognizable device image name")
    registry_digest = forms.RegexField(regex=r"^[a-zA-Z0-9._:/-]+@sha256:[a-fA-F0-9]{64}$", max_length=512,
        help_text="Immutable OCI reference, for example registry.example/frr@sha256:…")
    architecture = forms.ChoiceField(choices=(("amd64", "amd64"), ("arm64", "arm64")))
    vendor = forms.CharField(max_length=80, required=False)
    version = forms.CharField(max_length=80, required=False)
    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["project"].queryset = Project.objects.filter(Q(owner=user) | Q(memberships__user=user, memberships__role__in=("administrator", "editor"))).distinct()
