import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require=createRequire(import.meta.url);const {firefox}=require("playwright");
const baseUrl=(process.env.TRAINING_BASE_URL||"").replace(/\/$/,"");const username=process.env.TRAINING_USERNAME||"",password=process.env.TRAINING_PASSWORD||"";
const output=path.resolve(process.env.TEMPLATE_COMPATIBILITY_SCREENSHOT||"training/65-template-image-compatibility.png");
if(!baseUrl||!username||!password)throw new Error("Set TRAINING_BASE_URL, TRAINING_USERNAME, and TRAINING_PASSWORD.");
const browser=await firefox.launch({headless:true});const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1600,height:1100},colorScheme:"dark"});const page=await context.newPage();
try{
  await page.goto(`${baseUrl}/accounts/login/`,{waitUntil:"networkidle"});await page.locator("#id_username").fill(username);await page.locator("#id_password").fill(password);
  await Promise.all([page.waitForURL(url=>!url.pathname.includes("/accounts/login/")),page.getByRole("button",{name:/sign in|log in/i}).click()]);
  await page.goto(`${baseUrl}/device-templates/`,{waitUntil:"networkidle"});const templateRow=page.locator(".catalog-row").filter({hasText:"FRR Router"}).first();await templateRow.waitFor();
  const templateHref=await templateRow.locator('a[href^="/device-templates/"]').getAttribute("href");await page.goto(`${baseUrl}${templateHref}`,{waitUntil:"networkidle"});
  const matrix=page.locator(".compatibility-panel");await matrix.waitFor();const matrixText=await matrix.innerText();
  if(!matrixText.includes("Immutable identity")||!matrixText.includes("FRRouting")||!matrixText.includes("Compatible"))throw new Error("Production FRR compatibility evidence is incomplete");
  const compatibleRows=await matrix.locator(".compatibility-compatible").count();
  await page.screenshot({path:output,fullPage:true});
  await page.goto(`${baseUrl}/labs/`,{waitUntil:"networkidle"});const workspaceHrefs=await page.locator('a[href*="/workspace/"]').evaluateAll(links=>links.map(link=>link.getAttribute("href")).filter(Boolean));
  let workspaceHref="";for(const href of workspaceHrefs){const labId=href.match(/\/labs\/([0-9a-f-]+)\/workspace\//i)?.[1];if(!labId)continue;const document=await page.evaluate(async id=>(await fetch(`/api/v1/labs/${id}/topology/`,{cache:"no-store"})).json(),labId);if(document.nodes?.some(node=>node.name==="r1"&&node.publishedImageId)){workspaceHref=href;break}}
  if(!workspaceHref)throw new Error("No saved BGP draft with a pinned r1 image was available");await page.goto(`${baseUrl}${workspaceHref}`,{waitUntil:"networkidle"});
  const editor=page.frameLocator('iframe[title^="Topology workspace"]');await editor.locator(".workspace-shell").waitFor();
  const r1=editor.locator(".topology-device").filter({hasText:"r1"}).first();await r1.click();const selector=editor.locator(".properties select").first();
  const selected=await selector.locator("option:checked").innerText();const help=await selector.locator("xpath=following-sibling::small").innerText();
  if(!selected.includes("compatible")||!help.includes("compatible with this template"))throw new Error("Topology editor did not explain its compatible image decision");
  console.log(JSON.stringify({templateHref,compatibleRows,workspaceHref,selected,help,screenshot:output}));
}finally{await browser.close()}
