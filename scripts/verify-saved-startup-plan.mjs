import {createRequire} from "node:module";
import path from "node:path";
import process from "node:process";
const require=createRequire(import.meta.url);const {firefox}=require("playwright");
const baseUrl=(process.env.TRAINING_BASE_URL||"").replace(/\/$/,""),username=process.env.TRAINING_USERNAME||"",password=process.env.TRAINING_PASSWORD||"";
const originalDeploymentId=process.env.START_PLAN_DEPLOYMENT_ID||"",labId=process.env.START_PLAN_LAB_ID||"",output=path.resolve(process.env.START_PLAN_SCREENSHOT||"training/56-saved-topology-startup-plan.png");
if(!baseUrl||!username||!password||!originalDeploymentId||!labId)throw new Error("Set training URL, credentials, START_PLAN_DEPLOYMENT_ID, and START_PLAN_LAB_ID.");
const browser=await firefox.launch({headless:true});const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1600,height:1000},colorScheme:"dark"});
const page=await context.newPage(),browserErrors=[];page.on("console",message=>{if(message.type()==="error")browserErrors.push(`console: ${message.text()}`)});page.on("pageerror",error=>browserErrors.push(`page: ${error.message}`));page.on("response",response=>{if(response.status()>=400)browserErrors.push(`http ${response.status()}: ${response.url()}`)});
const api=async(pathname,options={})=>page.evaluate(async({pathname,options})=>{const csrf=document.cookie.split("; ").find(value=>value.startsWith("csrftoken="))?.split("=")[1]||"";const response=await fetch(pathname,{credentials:"same-origin",...options,headers:{"Content-Type":"application/json","X-CSRFToken":csrf,...options.headers}});return {status:response.status,data:await response.json()}},{pathname,options});
const waitRuntime=async(deploymentId,predicate,attempts=150)=>{for(let attempt=0;attempt<attempts;attempt+=1){await page.waitForTimeout(1000);const result=await api(`/api/v1/deployments/${deploymentId}/runtime/`);if(result.status===200&&predicate(result.data))return result.data}throw new Error("Runtime state did not converge before the acceptance deadline.")};
try{
  await page.goto(`${baseUrl}/accounts/login/`,{waitUntil:"domcontentloaded"});await page.locator("#id_username").fill(username);await page.locator("#id_password").fill(password);
  await Promise.all([page.waitForURL(url=>!url.pathname.includes("/accounts/login/")),page.getByRole("button",{name:/sign in|log in/i}).click()]);
  let original=await api(`/api/v1/deployments/${originalDeploymentId}/runtime/`);if(original.status!==200)throw new Error("Original BGP runtime is unavailable.");
  const labResult=await api(`/api/v1/labs/${labId}/`);if(labResult.status!==200)throw new Error("BGP Reference lab is unavailable.");const lab=labResult.data;
  if(original.data.deployment.observed_state!=="stopped"){
    const stopped=await api(`/api/v1/deployments/${originalDeploymentId}/operations/`,{method:"POST",headers:{"Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({operation:"stop_lab"})});if(stopped.status!==202)throw new Error(stopped.data.error?.details||"Could not stop the original runtime.");
    await waitRuntime(originalDeploymentId,data=>data.deployment.observed_state==="stopped",120);
  }
  const lease=await api(`/api/v1/labs/${lab.id}/topology/edit-lease/`,{method:"POST",body:"{}"});if(lease.status!==200)throw new Error(lease.data.error?.details||"Could not acquire the topology editing session.");
  const revisions=await api(`/api/v1/labs/${lab.id}/revisions/`),source=revisions.data.revisions.find(revision=>revision.immutable&&revision.revision_number===2)||revisions.data.revisions.find(revision=>revision.immutable);
  if(!source)throw new Error("No immutable BGP revision is available for restore.");
  const restored=await api(`/api/v1/labs/${lab.id}/revisions/${source.id}/restore/`,{method:"POST",headers:{"Idempotency-Key":crypto.randomUUID(),"X-Edit-Lease":lease.data.token},body:JSON.stringify({expected_current_draft:revisions.data.current_draft})});
  if(![200,201].includes(restored.status))throw new Error(restored.data.error?.details||"Revision restore failed.");
  const topology=await api(`/api/v1/labs/${lab.id}/topology/`);for(const node of topology.data.nodes)node.properties={...node.properties,startupOrder:node.name==="r2"?10:node.name==="r1"?20:null};
  const saved=await api(`/api/v1/labs/${lab.id}/topology/`,{method:"PUT",headers:{"X-Edit-Lease":lease.data.token},body:JSON.stringify(topology.data)});if(saved.status!==200)throw new Error(saved.data.error||"Saving startup priorities failed.");
  const deployed=await api(`/api/v1/labs/${lab.id}/deploy/`,{method:"POST",headers:{"Idempotency-Key":crypto.randomUUID(),"X-Edit-Lease":lease.data.token},body:"{}"});if(deployed.status!==202)throw new Error(deployed.data.error?.details||"Saved-plan revision deployment failed.");
  const deploymentId=deployed.data.deployment.id;let current=await waitRuntime(deploymentId,data=>data.deployment.observed_state==="running"&&data.devices.length===2&&data.devices.every(device=>device.observed_readiness==="ready"),180);
  if(current.devices.find(device=>device.name==="r2").startup_order!==10||current.devices.find(device=>device.name==="r1").startup_order!==20)throw new Error("Runtime API did not preserve saved startup priorities.");
  await page.goto(`${baseUrl}/deployments/${deploymentId}/`,{waitUntil:"domcontentloaded"});await page.locator("[data-device-select]").first().waitFor({state:"visible",timeout:20000});
  for(const device of current.devices)await page.locator(`[data-device-select][value="${device.id}"]`).check();await page.locator('[data-bulk-device-operation="stop_device"]').click();await page.locator("#bulk-device-dialog").waitFor({state:"visible"});await page.locator("#confirm-bulk-device").click();
  current=await waitRuntime(deploymentId,data=>data.devices.every(device=>device.observed_readiness==="stopped"&&device.runtime_resources.manual_desired_state==="stopped"),120);
  await page.locator("#refresh-runtime").click();await page.waitForTimeout(1000);const savedButton=page.locator("#saved-start-plan");if(await savedButton.isDisabled())throw new Error("Saved startup plan remained disabled after both devices stopped.");await savedButton.click();
  const dialog=page.locator("#staged-start-dialog");await dialog.waitFor({state:"visible"});await page.locator("#staged-start-interval").fill("8");const displayed=await page.locator("#staged-start-list strong").allTextContents();if(displayed.join(",")!=="r2,r1")throw new Error(`Saved order rendered as ${displayed.join(",")}`);await dialog.screenshot({path:output});
  const existing=new Set(current.operations.map(operation=>operation.id));await page.locator("#confirm-staged-start").click();let completed;
  current=await waitRuntime(deploymentId,data=>{completed=data.operations.find(operation=>!existing.has(operation.id)&&operation.operation_type==="staged_start_devices");if(completed?.state==="failed")throw new Error(completed.error_details?.message||"Saved startup plan failed.");return completed?.state==="succeeded"&&data.devices.every(device=>device.observed_readiness==="ready")},180);
  const rows=completed.result_payload.devices,separation=(new Date(rows[1].started_at)-new Date(rows[0].started_at))/1000;if(rows.map(row=>row.device).join(",")!=="r2,r1"||separation<7.5)throw new Error("Worker did not preserve the saved order and interval.");
  await page.waitForTimeout(12000);const r1=current.devices.find(device=>device.name==="r1");await page.locator("#ping-node").selectOption(r1.node_id);await page.locator("#ping-target").fill("10.2.2.2");await page.locator("#ping-form button").click();let diagnostic;
  await waitRuntime(deploymentId,data=>{diagnostic=data.operations.find(operation=>operation.operation_type==="ping"&&!existing.has(operation.id));return diagnostic?.state==="succeeded"},45);if(!/0% packet loss/.test(diagnostic.result_payload.output))throw new Error("BGP reachability did not recover after the saved plan.");
  if(browserErrors.length)throw new Error(`Firefox reported errors: ${browserErrors.join(" | ")}`);console.log(JSON.stringify({browser:"firefox",lab:lab.name,revision:restored.data.revision_number,deploymentId,order:rows.map(row=>row.device),intervalSeconds:8,separationSeconds:separation,reachability:"3/3, 0% loss",screenshot:output}));
}finally{await browser.close()}
