from email.message import Message
import pytest

from studio.tasks import probe_registry_health


class RegistryResponse:
    status=200
    headers=Message()

    def __enter__(self):
        self.headers["Docker-Distribution-Api-Version"]="registry/2.0"
        return self

    def __exit__(self,*_): return False
    def read(self,_): return b"{}"


def test_registry_probe_records_verified_distribution_api(settings,monkeypatch):
    settings.REGISTRY_INTERNAL_URL="http://registry:5000"
    recorded={}
    monkeypatch.setattr("studio.tasks.urlopen",lambda request,timeout:RegistryResponse())
    monkeypatch.setattr("studio.tasks.publish_platform_health",lambda key,payload:recorded.update(key=key,payload=payload))
    result=probe_registry_health.run()
    assert result["available"] is True and result["api_version"]=="registry/2.0"
    assert recorded["key"]=="studio:platform:registry"


def test_registry_probe_reports_failure_without_raising(settings,monkeypatch):
    settings.REGISTRY_INTERNAL_URL="http://registry:5000"
    monkeypatch.setattr("studio.tasks.urlopen",lambda *_args,**_kwargs:(_ for _ in ()).throw(OSError("connection refused")))
    result=probe_registry_health.run()
    assert result["available"] is False and "connection refused" in result["reason"]
