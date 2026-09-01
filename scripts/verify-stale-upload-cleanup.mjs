import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require=createRequire(import.meta.url);const { firefox }=require("playwright");
const baseUrl=(process.env.TRAINING_BASE_URL||"").replace(/\/$/,"");const username=process.env.TRAINING_USERNAME||"";const password=process.env.TRAINING_PASSWORD||"";
const deploymentId=process.env.UPLOAD_CLEANUP_DEPLOYMENT_ID||"";const sessionId=process.env.UPLOAD_CLEANUP_SESSION_ID||"";
const output=path.resolve(process.env.UPLOAD_CLEANUP_SCREENSHOT||"training/62-stale-upload-cleanup.png");
if(!baseUrl||!username||!password)throw new Error("Set the training URL and credentials.");
await mkdir(path.dirname(output),{recursive:true});const browser=await firefox.launch({headless:true});
const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1600,height:1000},colorScheme:"dark"});const page=await context.newPage();const errors=[];
page.on("pageerror",error=>errors.push(error.message));page.on("response",response=>{if(response.status()>=500)errors.push(`${response.status()} ${response.url()}`)});
try{
  await page.goto(`${baseUrl}/accounts/login/`,{waitUntil:"domcontentloaded"});await page.locator("#id_username").fill(username);await page.locator("#id_password").fill(password);
  await Promise.all([page.waitForURL(url=>!url.pathname.includes("/accounts/login/")),page.getByRole("button",{name:/sign in|log in/i}).click()]);
  if(!sessionId){
    await page.goto(`${baseUrl}/images/upload/`,{waitUntil:"domcontentloaded"});const options=await page.locator("#upload-project option").evaluateAll(rows=>rows.map(row=>({value:row.value,label:row.textContent?.trim()||""})).filter(row=>row.value));
    const project=options.find(row=>row.label==="Production Runtime QA")||options[0];if(!project)throw new Error("No editable project is available.");
    const created=await page.evaluate(async({projectId})=>{const csrf=document.cookie.match(/csrftoken=([^;]+)/)?.[1]||"";
      const response=await fetch("/api/v1/uploads/",{method:"POST",headers:{"Content-Type":"application/json","X-CSRFToken":csrf},body:JSON.stringify({project:projectId,original_filename:`stale-training-${Date.now()}.tar`,expected_size:4096,expected_checksum:"",license_acknowledged:true})});
      const session=await response.json();if(!response.ok)throw new Error(JSON.stringify(session));
      const chunk=await fetch(`/api/v1/uploads/${session.id}/chunks/`,{method:"PUT",headers:{"Content-Type":"application/octet-stream","Upload-Offset":"0","X-CSRFToken":csrf},body:new Uint8Array(1024)});
      if(!chunk.ok)throw new Error(await chunk.text());return {...session,received_bytes:Number(chunk.headers.get("Upload-Offset"))};},{projectId:project.value});
    if(created.received_bytes!==1024)throw new Error("Partial upload offset did not advance.");
    console.log(JSON.stringify({phase:"created",browser:"firefox",sessionId:created.id,filename:created.original_filename,expectedSize:created.expected_size,receivedBytes:created.received_bytes,project:project.label}));
  }else{
  if(!deploymentId)throw new Error("Set UPLOAD_CLEANUP_DEPLOYMENT_ID during verification.");
  await page.goto(`${baseUrl}/images/upload/`,{waitUntil:"domcontentloaded"});const row=page.locator(".upload-history-row").filter({hasText:sessionId});await row.waitFor({timeout:15000});
  const text=(await row.textContent())||"";if(!text.includes("Expired")||!text.includes("Released")||!/1\.0\s*KB\s*\/\s*4\.0\s*KB/.test(text))throw new Error(`Cleanup history is incomplete: ${text}`);
  const upload=await(await context.request.get(`${baseUrl}/api/v1/uploads/${sessionId}/`,{failOnStatusCode:true})).json();
  if(upload.status!=="expired"||upload.cleanup_result?.storage_removed!==true||upload.cleanup_result?.received_bytes!==1024)throw new Error("Cleanup result was not persisted.");
  if("artifact_destination" in upload||JSON.stringify(upload).includes("/artifacts/"))throw new Error("Upload API exposed its internal quarantine path.");
  const audit=await(await context.request.get(`${baseUrl}/audit/?action=image.upload_expired&target=${sessionId}`,{failOnStatusCode:true})).text();if(!audit.includes("image.upload_expired")||!audit.includes(sessionId))throw new Error("System expiry audit evidence was not visible.");
  const runtime=await(await context.request.get(`${baseUrl}/api/v1/deployments/${deploymentId}/runtime/`,{failOnStatusCode:true})).json();const ready=runtime.devices.filter(device=>["r1","r2"].includes(device.name)&&device.observed_readiness==="ready");if(ready.length!==2)throw new Error("Topology health changed during upload cleanup.");
  await row.screenshot({path:output});if(errors.length)throw new Error(`Firefox reported errors: ${errors.join(" | ")}`);
  console.log(JSON.stringify({phase:"verified",browser:"firefox",sessionId,status:upload.status,receivedBytes:upload.received_bytes,expectedSize:upload.expected_size,storageRemoved:upload.cleanup_result.storage_removed,
    internalPathHidden:true,auditVisible:true,topologyDevicesReady:ready.map(device=>device.name),screenshot:output}));
  }
}finally{await browser.close()}
