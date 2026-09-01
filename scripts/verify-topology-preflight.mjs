import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require=createRequire(import.meta.url);const {firefox}=require("playwright");
const baseUrl=(process.env.TRAINING_BASE_URL||"").replace(/\/$/,"");const username=process.env.TRAINING_USERNAME||"",password=process.env.TRAINING_PASSWORD||"";
const output=path.resolve(process.env.TOPOLOGY_PREFLIGHT_SCREENSHOT||"training/67-server-topology-preflight.png");
if(!baseUrl||!username||!password)throw new Error("Set TRAINING_BASE_URL, TRAINING_USERNAME, and TRAINING_PASSWORD.");
const browser=await firefox.launch({headless:true});const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1600,height:1100},colorScheme:"dark"});const page=await context.newPage();
try{
  await page.goto(`${baseUrl}/accounts/login/`,{waitUntil:"networkidle"});await page.locator("#id_username").fill(username);await page.locator("#id_password").fill(password);
  await Promise.all([page.waitForURL(url=>!url.pathname.includes("/accounts/login/")),page.getByRole("button",{name:/sign in|log in/i}).click()]);
  await page.goto(`${baseUrl}/labs/`,{waitUntil:"networkidle"});const hrefs=await page.locator('a[href*="/workspace/"]').evaluateAll(links=>links.map(link=>link.getAttribute("href")).filter(Boolean));
  let workspaceHref="",report=null;for(const href of hrefs){const labId=href.match(/\/labs\/([0-9a-f-]+)\/workspace\//i)?.[1];if(!labId)continue;
    const result=await page.evaluate(async id=>{const response=await fetch(`/api/v1/labs/${id}/validation-report/`,{cache:"no-store"});return response.ok?response.json():null},labId);
    if(result?.ready&&result.devices?.length>=2){workspaceHref=href;report=result;break}}
  if(!workspaceHref||!report)throw new Error("No deployment-ready saved multi-device draft was available");
  await page.goto(`${baseUrl}${workspaceHref}`,{waitUntil:"networkidle"});const editor=page.frameLocator('iframe[title^="Topology workspace"]');await editor.locator(".workspace-shell").waitFor();
  await editor.getByRole("button",{name:/^Validate/}).click();const dialog=editor.getByRole("dialog",{name:"Deployment readiness report"});await dialog.waitFor();const text=(await dialog.innerText()).toLowerCase();
  for(const phrase of ["server-authoritative preflight","ready to deploy","6 / 6 checks","immutable device images","startup configuration policy","clabernetes adapter preflight","device evidence","clabernetes 0.8.0"]){if(!text.includes(phrase))throw new Error(`Preflight report is missing: ${phrase}`)}
  if(await dialog.locator(".preflight-checks article.failed").count())throw new Error("A ready report rendered a failed platform check");
  if(await dialog.locator(".preflight-devices>article").count()!==report.devices.length)throw new Error("Per-device evidence count did not match the server report");
  await page.screenshot({path:output,fullPage:true});await dialog.getByRole("button",{name:"Close report"}).click();
  console.log(JSON.stringify({workspaceHref,lab:report.lab,revision:report.revision.number,devices:report.summary.devices,links:report.summary.links,checks:`${report.summary.passed_checks}/${report.summary.total_checks}`,screenshot:output,mutation:"none"}));
}finally{await browser.close()}
