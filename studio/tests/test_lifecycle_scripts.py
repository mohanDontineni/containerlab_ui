import os
import subprocess
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[2]

@pytest.fixture
def fake_cluster_tools(tmp_path):
    bin_dir=tmp_path/"bin";bin_dir.mkdir();log=tmp_path/"calls.log"
    kubectl=bin_dir/"kubectl";kubectl.write_text("""#!/bin/sh
echo "kubectl $*" >>"$CALL_LOG"
case "$*" in
  "config current-context") echo test-context;;
  "get nodes") echo 'node/test Ready';;
  "get svc -A"*) :;;
  "auth can-i create deployments"*) echo yes;;
  "get apiservice v1beta1.metrics.k8s.io"*) echo True;;
  "get namespaces -l app.kubernetes.io/managed-by=containerlab-studio"*) printf 'clab-one\\nclab-two\\n';;
  *) :;;
esac
""")
    helm=bin_dir/"helm";helm.write_text("""#!/bin/sh
echo "helm $*" >>"$CALL_LOG"
case "$1" in
  template) echo 'apiVersion: v1';echo 'kind: List';;
  status) exit 0;;
  *) :;;
esac
""")
    kubectl.chmod(0o755);helm.chmod(0o755)
    env={**os.environ,"PATH":f"{bin_dir}:{os.environ['PATH']}","CALL_LOG":str(log),"KUBE_CONTEXT":"test-context","REQUIRE_METRICS_API":"true"}
    return env,log

def run(script,*args,env):
    return subprocess.run([str(ROOT/"scripts"/script),*args],cwd=ROOT,env=env,text=True,capture_output=True)

def test_install_plan_is_default_non_mutating_and_records_rendered_intent(fake_cluster_tools,tmp_path):
    env,log=fake_cluster_tools;plan=tmp_path/"plan.yaml";env={**env,"PLAN_OUTPUT":str(plan),"IMAGE_REPOSITORY":"registry.example/studio","IMAGE_TAG":"sha-abc","STORAGE_CLASS":"studio-local"}
    result=run("install.sh",env=env)
    assert result.returncode==0 and "PLAN ONLY" in result.stdout and "registry.example/studio:sha-abc" in result.stdout
    assert plan.read_text()=="apiVersion: v1\nkind: List\n"
    calls=log.read_text();assert "helm lint" in calls and "helm template" in calls
    assert all(word not in calls for word in ("helm upgrade","helm uninstall","kubectl apply","kubectl delete"))

def test_uninstall_plan_lists_exact_owned_runtimes_and_retained_data_without_mutation(fake_cluster_tools):
    env,log=fake_cluster_tools;result=run("uninstall.sh","plan",env=env)
    assert result.returncode==0 and "clab-one" in result.stdout and "clab-two" in result.stdout
    assert "containerlab-studio-postgres" in result.stdout and "PLAN ONLY" in result.stdout
    calls=log.read_text();assert "helm uninstall" not in calls and "kubectl delete" not in calls

def test_purge_requires_exact_confirmation_before_any_mutation(fake_cluster_tools):
    env,log=fake_cluster_tools;result=run("uninstall.sh","purge",env=env)
    assert result.returncode==6 and "Purge refused" in result.stderr
    calls=log.read_text();assert "helm uninstall" not in calls and "kubectl delete" not in calls

def test_context_mismatch_fails_closed_before_helm(fake_cluster_tools):
    env,log=fake_cluster_tools;env={**env,"KUBE_CONTEXT":"different-context"};result=run("install.sh","plan",env=env)
    assert result.returncode==2 and "does not match" in result.stderr and "helm " not in log.read_text()

def test_chart_retains_every_persistent_claim_and_owned_registry_volume():
    data=(ROOT/"helm/containerlab-studio/templates/data.yaml").read_text()
    assert data.count("helm.sh/resource-policy: keep")==5
    for name in ("containerlab-studio-postgres","containerlab-studio-redis","containerlab-studio-artifacts","containerlab-studio-registry","containerlab-studio-registry-pv"):
        assert name in data
