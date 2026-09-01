from django import forms
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db.models import Q
from zoneinfo import available_timezones
from .models import Lab, Project, User

class ProfileForm(forms.ModelForm):
    timezone=forms.ChoiceField(choices=[(value,value) for value in sorted(available_timezones())])
    class Meta:
        model=User
        fields=("first_name","last_name","email","timezone")
        widgets={
            "first_name":forms.TextInput(attrs={"autocomplete":"given-name"}),
            "last_name":forms.TextInput(attrs={"autocomplete":"family-name"}),
            "email":forms.EmailInput(attrs={"autocomplete":"email"}),
        }
    def clean_email(self): return self.cleaned_data["email"].strip().lower()

class StudioPasswordChangeForm(PasswordChangeForm):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields["old_password"].widget.attrs.update({"autocomplete":"current-password"})
        self.fields["new_password1"].widget.attrs.update({"autocomplete":"new-password"})
        self.fields["new_password2"].widget.attrs.update({"autocomplete":"new-password"})

class PlatformUserCreateForm(forms.ModelForm):
    password1=forms.CharField(label="Temporary password",widget=forms.PasswordInput(attrs={"autocomplete":"new-password"}),help_text="At least 12 characters. Share it through a secure channel.")
    password2=forms.CharField(label="Confirm temporary password",widget=forms.PasswordInput(attrs={"autocomplete":"new-password"}))
    class Meta:
        model=User
        fields=("username","first_name","last_name","email","timezone")
        widgets={"username":forms.TextInput(attrs={"autocomplete":"off"}),"email":forms.EmailInput(attrs={"autocomplete":"email"})}
    def clean_username(self): return self.cleaned_data["username"].strip()
    def clean_email(self): return self.cleaned_data["email"].strip().lower()
    def clean(self):
        cleaned=super().clean();first=cleaned.get("password1");second=cleaned.get("password2")
        if first and second and first!=second: self.add_error("password2","The passwords do not match.")
        if first:
            candidate=User(username=cleaned.get("username",""),email=cleaned.get("email",""),first_name=cleaned.get("first_name",""),last_name=cleaned.get("last_name",""))
            try: validate_password(first,candidate)
            except ValidationError as exc: self.add_error("password1",exc)
        return cleaned

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
        self.fields["project"].queryset = Project.objects.filter(Q(owner=user) | Q(memberships__user=user, memberships__role__in=("administrator", "editor")),deleted_at__isnull=True).distinct()

class LabEditForm(forms.ModelForm):
    class Meta:
        model=Lab
        fields=("name","description","tags")
        widgets={"name":forms.TextInput(),"description":forms.Textarea(attrs={"rows":4}),
            "tags":forms.TextInput(attrs={"placeholder":'["bgp", "edge"]'})}

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
        self.fields["project"].queryset = Project.objects.filter(Q(owner=user) | Q(memberships__user=user, memberships__role__in=("administrator", "editor")),deleted_at__isnull=True).distinct()
