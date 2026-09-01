import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require=createRequire(import.meta.url);const {firefox}=require("playwright");
const baseUrl=(process.env.TRAINING_BASE_URL||"").replace(/\/$/,"");
const username=process.env.TRAINING_USERNAME||"",password=process.env.TRAINING_PASSWORD||"";
const deploymentId=process.env.CONFIGURATION_DEPLOYMENT_ID||"";
const output=path.resolve(process.env.CONFIGURATION_CHECKPOINT_SCREENSHOT||"training/74-whole-lab-running-configuration-checkpoint.png");
if(!baseUrl||!username||!password||!deploymentId)throw new Error("Set training login and CONFIGURATION_DEPLOYMENT_ID values.");
await mkdir(path.dirname(output),{recursive:true});
const browser=await firefox.launch({headless:true});const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1600,height:1100},colorScheme:"dark"});const page=await context.newPage();const errors=[];
page.on("pageerror",error=>errors.push(error.message));page.on("response",response=>{if(response.status()>=500)errors.push(`${response.status()} ${response.url()}`)});
const runtime=async()=>await (await context.request.get(`${baseUrl}/api/v1/deployments/${deploymentId}/runtime/`,{failOnStatusCode:true})).json();
try{
  await page.goto(`${baseUrl}/accounts/login/`,{waitUntil:"domcontentloaded"});await page.locator("#id_username").fill(username);await page.locator("#id_password").fill(password);
  await Promise.all([page.waitForURL(url=>!url.pathname.includes("/accounts/login/")),page.getByRole("button",{name:/sign in|log in/i}).click()]);
  const before=await runtime(),beforePods=Object.fromEntries(before.devices.map(device=>[device.name,device.runtime_resources.pod_uid]));
  await page.goto(`${baseUrl}/deployments/${deploymentId}/`,{waitUntil:"domcontentloaded"});
  const previewPromise=page.waitForResponse(response=>response.url().endsWith(`/deployments/${deploymentId}/configurations/save-preview/`));await page.locator("#save-configurations").click();
  const previewResponse=await previewPromise,preview=await previewResponse.json();if(previewResponse.status()!==200||!preview.can_save||preview.device_count!==2)throw new Error(`Checkpoint preview unavailable: ${JSON.stringify(preview)}`);
  const dialog=page.locator("#configuration-save-dialog");await dialog.waitFor({state:"visible"});const dialogText=await dialog.innerText();
  const previewVersions=Object.fromEntries(preview.configurations.map(row=>[row.device,row.version]));
  if(Number(previewVersions.r1)!==2||Number(previewVersions.r2)!==2||!dialogText.toLowerCase().includes("runtime")||!dialogText.includes("Unchanged"))throw new Error(`Checkpoint preview did not show both v2 routers and unchanged runtime impact: ${dialogText}`);
  await dialog.screenshot({path:output});
  const savePromise=page.waitForResponse(response=>response.url().endsWith(`/deployments/${deploymentId}/configurations/save/`)&&response.request().method()==="POST");
  await dialog.locator("#confirm-configuration-save").click();const saveResponse=await savePromise,result=await saveResponse.json();if(saveResponse.status()!==201)throw new Error(`Checkpoint save failed: ${JSON.stringify(result)}`);
  await page.waitForURL(url=>url.pathname===new URL(result.workspace_url,baseUrl).pathname);
  const after=await runtime(),afterPods=Object.fromEntries(after.devices.map(device=>[device.name,device.runtime_resources.pod_uid]));
  if(after.deployment.revision!==before.deployment.revision||after.deployment.observed_state!=="running"||JSON.stringify(afterPods)!==JSON.stringify(beforePods))throw new Error("Running deployment or launcher identities changed during checkpoint creation.");
  const labId=new URL(result.workspace_url,baseUrl).pathname.split("/")[2],revisions=await (await context.request.get(`${baseUrl}/api/v1/labs/${labId}/revisions/`,{failOnStatusCode:true})).json();
  if(revisions.current_draft!==result.revision_id)throw new Error("New checkpoint is not the active editable draft.");
  if(errors.length)throw new Error(`Firefox reported errors: ${errors.join(" | ")}`);
  console.log(JSON.stringify({browser:"Firefox",sourceRevision:before.deployment.revision,draftRevision:result.revision_number,deviceCount:result.device_count,podsPreserved:afterPods,screenshot:output}));
}finally{await browser.close()}
