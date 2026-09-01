import hashlib
import re
from django import forms
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db.models import Q
from zoneinfo import available_timezones
from .configurations import encrypt_secret
from .models import ImageCredentialReference, Lab, LabFolder, Project, User

def editable_projects(user):
    return Project.objects.filter(Q(owner=user) | Q(memberships__user=user, memberships__role__in=("administrator", "editor")),deleted_at__isnull=True).distinct()

class LabFolderForm(forms.ModelForm):
    class Meta:
        model=LabFolder;fields=("project","parent","name")
        widgets={"name":forms.TextInput(attrs={"placeholder":"e.g. Routing / BGP"})}
    def __init__(self,user,*args,**kwargs):
        super().__init__(*args,**kwargs);projects=editable_projects(user);self.fields["project"].queryset=projects
        folders=LabFolder.objects.filter(project__in=projects,deleted_at__isnull=True).select_related("parent").order_by("project__name","name")
        if self.instance.pk: folders=folders.exclude(pk=self.instance.pk)
        self.fields["parent"].queryset=folders
        if not self.instance._state.adding: self.fields["project"].disabled=True
    def clean(self):
        cleaned=super().clean();project=cleaned.get("project");parent=cleaned.get("parent")
        if not self.instance._state.adding and project and project.id!=LabFolder.objects.only("project_id").get(pk=self.instance.pk).project_id:
            self.add_error("project","A lab folder cannot be moved between projects.")
        if project and parent and parent.project_id!=project.id: self.add_error("parent","Choose a folder in the selected project.")
        return cleaned

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

class PlatformPasswordResetForm(forms.Form):
    password1=forms.CharField(label="New temporary password",widget=forms.PasswordInput(attrs={"autocomplete":"new-password"}))
    password2=forms.CharField(label="Confirm temporary password",widget=forms.PasswordInput(attrs={"autocomplete":"new-password"}))
    def __init__(self,user,*args,**kwargs): self.user=user;super().__init__(*args,**kwargs)
    def clean(self):
        cleaned=super().clean();first=cleaned.get("password1");second=cleaned.get("password2")
        if first and second and first!=second: self.add_error("password2","The passwords do not match.")
        if first:
            try: validate_password(first,self.user)
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
        fields = ("project", "folder", "name", "description", "tags")
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. BGP Edge Validation"}),
            "description": forms.Textarea(attrs={"rows": 4, "placeholder": "Describe the lab objective and expected outcome"}),
            "tags": forms.TextInput(attrs={"placeholder": '["bgp", "edge"]'}),
        }
    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        projects=editable_projects(user);self.fields["project"].queryset=projects
        self.fields["folder"].queryset=LabFolder.objects.filter(project__in=projects,deleted_at__isnull=True).select_related("project","parent").order_by("project__name","name")
    def clean(self):
        cleaned=super().clean();project=cleaned.get("project");folder=cleaned.get("folder")
        if project and folder and folder.project_id!=project.id: self.add_error("folder","Choose a folder in the selected project.")
        return cleaned

class LabEditForm(forms.ModelForm):
    class Meta:
        model=Lab
        fields=("folder","name","description","tags")
        widgets={"name":forms.TextInput(),"description":forms.Textarea(attrs={"rows":4}),
            "tags":forms.TextInput(attrs={"placeholder":'["bgp", "edge"]'})}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields["folder"].queryset=LabFolder.objects.filter(project=self.instance.project,deleted_at__isnull=True).select_related("parent").order_by("name")

class RegistryImageForm(forms.Form):
    project = forms.ModelChoiceField(queryset=Project.objects.none())
    name = forms.CharField(max_length=120, help_text="A recognizable device image name")
    registry_digest = forms.RegexField(regex=r"^[a-zA-Z0-9._:/-]+@sha256:[a-fA-F0-9]{64}$", max_length=512,
        help_text="Immutable OCI reference, for example registry.example/frr@sha256:…")
    architecture = forms.ChoiceField(choices=(("amd64", "amd64"), ("arm64", "arm64")))
    vendor = forms.CharField(max_length=80, required=False)
    version = forms.CharField(max_length=80, required=False)
    credential_reference = forms.ModelChoiceField(queryset=ImageCredentialReference.objects.none(),required=False,
        help_text="Optional protected credential for this registry host. Secrets are never returned to the browser.")
    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        projects=editable_projects(user);self.fields["project"].queryset=projects
        self.fields["credential_reference"].queryset=ImageCredentialReference.objects.filter(project__in=projects,is_active=True).select_related("project").order_by("project__name","name")
    def clean(self):
        cleaned=super().clean();project=cleaned.get("project");credential=cleaned.get("credential_reference");reference=cleaned.get("registry_digest","")
        if credential and project and credential.project_id!=project.id: self.add_error("credential_reference","Choose a credential from the selected project.")
        if credential and reference:
            first=reference.split("/",1)[0].lower();host=first if "." in first or ":" in first or first=="localhost" else "docker.io"
            if credential.registry_host!=host: self.add_error("credential_reference",f"This reference resolves to {host}, not {credential.registry_host}.")
        return cleaned

class RegistryCredentialForm(forms.ModelForm):
    secret=forms.CharField(max_length=4096,widget=forms.PasswordInput(attrs={"autocomplete":"new-password"}),
        help_text="Stored encrypted. Leave blank while editing to keep the current secret.")
    class Meta:
        model=ImageCredentialReference
        fields=("project","name","registry_host","credential_type","username","is_active")
        widgets={"registry_host":forms.TextInput(attrs={"placeholder":"registry.example.com:5000","autocomplete":"off"}),
            "username":forms.TextInput(attrs={"autocomplete":"off"})}
    def __init__(self,user,*args,**kwargs):
        super().__init__(*args,**kwargs);self.user=user;self.fields["project"].queryset=editable_projects(user)
        self.fields["secret"].required=self.instance._state.adding
        if not self.instance._state.adding: self.fields["project"].disabled=True
    def clean_registry_host(self):
        value=self.cleaned_data["registry_host"].strip().lower()
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[1-9][0-9]{0,4})?",value) or ".." in value:
            raise ValidationError("Enter a registry hostname with an optional port, without a URL scheme or path.")
        if ":" in value and int(value.rsplit(":",1)[1])>65535: raise ValidationError("Registry port must be between 1 and 65535.")
        if not self.instance._state.adding and self.instance.image_artifacts.exists() and value!=self.instance.registry_host:
            raise ValidationError("The registry host cannot change while images reference this credential.")
        return value
    def clean(self):
        cleaned=super().clean()
        if cleaned.get("credential_type")==ImageCredentialReference.CredentialType.BASIC and not cleaned.get("username","").strip():
            self.add_error("username","A username is required for basic authentication.")
        return cleaned
    def save(self,commit=True):
        credential=super().save(commit=False);secret=self.cleaned_data.get("secret")
        if secret:
            credential.encrypted_secret=encrypt_secret(secret);credential.secret_fingerprint=hashlib.sha256(secret.encode()).hexdigest()[:16]
        if credential._state.adding: credential.created_by=self.user
        if commit: credential.save()
        return credential
