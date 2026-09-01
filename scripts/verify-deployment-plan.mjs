import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require=createRequire(import.meta.url);const {firefox}=require("playwright");
const baseUrl=(process.env.TRAINING_BASE_URL||"").replace(/\/$/,"");const username=process.env.TRAINING_USERNAME||"",password=process.env.TRAINING_PASSWORD||"";
const output=path.resolve(process.env.DEPLOYMENT_PLAN_SCREENSHOT||"training/66-explicit-deployment-plan.png");
if(!baseUrl||!username||!password)throw new Error("Set TRAINING_BASE_URL, TRAINING_USERNAME, and TRAINING_PASSWORD.");
const browser=await firefox.launch({headless:true});const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1600,height:1100},colorScheme:"dark"});const page=await context.newPage();
try{
  await page.goto(`${baseUrl}/accounts/login/`,{waitUntil:"networkidle"});await page.locator("#id_username").fill(username);await page.locator("#id_password").fill(password);
  await Promise.all([page.waitForURL(url=>!url.pathname.includes("/accounts/login/")),page.getByRole("button",{name:/sign in|log in/i}).click()]);
  await page.goto(`${baseUrl}/labs/`,{waitUntil:"networkidle"});const hrefs=await page.locator('a[href*="/workspace/"]').evaluateAll(links=>links.map(link=>link.getAttribute("href")).filter(Boolean));
  let workspaceHref="",plan=null;for(const href of hrefs){const labId=href.match(/\/labs\/([0-9a-f-]+)\/workspace\//i)?.[1];if(!labId)continue;
    const result=await page.evaluate(async id=>{const response=await fetch(`/api/v1/labs/${id}/deploy-preview/`,{cache:"no-store"});return response.ok?response.json():null},labId);
    if(result?.draft&&result.active_runtimes?.length){workspaceHref=href;plan=result;break}}
  if(!workspaceHref||!plan)throw new Error("No saved draft with an active pinned runtime was available");
  await page.goto(`${baseUrl}${workspaceHref}`,{waitUntil:"networkidle"});const editor=page.frameLocator('iframe[title^="Topology workspace"]');await editor.locator(".workspace-shell").waitFor();
  await editor.getByRole("button",{name:/Deploy$/}).click();const dialog=editor.getByRole("dialog",{name:"Review deployment plan"});await dialog.waitFor();const text=await dialog.innerText();
  for(const phrase of ["publish the saved draft","new clabernetes runtime","existing runtimes stay unchanged","pinned revisions","capacity after","no yaml is required"]){if(!text.toLowerCase().includes(phrase))throw new Error(`Deployment plan is missing: ${phrase}`)}
  const acknowledgement=dialog.getByRole("checkbox");if(await acknowledgement.isChecked())throw new Error("Existing-runtime acknowledgement must default to unchecked");
  const publish=dialog.getByRole("button",{name:"Publish and create new runtime"});if(await publish.isEnabled())throw new Error("Publishing must be blocked until the operator acknowledges the active runtime impact");
  await page.screenshot({path:output,fullPage:true});await acknowledgement.check();
  if(plan.can_deploy&&!(await publish.isEnabled()))throw new Error("Acknowledgement did not enable the verified deployment action");
  if(!plan.can_deploy&&(await publish.isEnabled()))throw new Error("A server-blocked deployment became actionable after acknowledgement");
  await dialog.getByRole("button",{name:"Cancel"}).click();
  console.log(JSON.stringify({workspaceHref,draftRevision:plan.draft.revision,activeRuntimes:plan.active_runtimes.length,capacity:plan.capacity,canDeploy:plan.can_deploy,issues:plan.issues,screenshot:output,mutation:"none"}));
}finally{await browser.close()}
