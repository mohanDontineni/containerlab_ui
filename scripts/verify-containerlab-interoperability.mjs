import { createRequire } from "node:module";
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";

const require=createRequire(import.meta.url);const {firefox}=require("playwright");
const baseUrl=(process.env.TRAINING_BASE_URL||"").replace(/\/$/,"");const username=process.env.TRAINING_USERNAME||"",password=process.env.TRAINING_PASSWORD||"";
const output=path.resolve(process.env.CONTAINERLAB_INTEROP_SCREENSHOT||"training/68-containerlab-interoperability.png");
if(!baseUrl||!username||!password)throw new Error("Set TRAINING_BASE_URL, TRAINING_USERNAME, and TRAINING_PASSWORD.");
const browser=await firefox.launch({headless:true});const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1600,height:1100},colorScheme:"dark",acceptDownloads:true});const page=await context.newPage();
try{
  await page.goto(`${baseUrl}/accounts/login/`,{waitUntil:"networkidle"});await page.locator("#id_username").fill(username);await page.locator("#id_password").fill(password);
  await Promise.all([page.waitForURL(url=>!url.pathname.includes("/accounts/login/")),page.getByRole("button",{name:/sign in|log in/i}).click()]);
  await page.goto(`${baseUrl}/labs/`,{waitUntil:"networkidle"});const hrefs=await page.locator('a[href*="/workspace/"]').evaluateAll(links=>links.map(link=>link.getAttribute("href")).filter(Boolean));
  let workspaceHref="",report=null;for(const href of hrefs){const labId=href.match(/\/labs\/([0-9a-f-]+)\/workspace\//i)?.[1];if(!labId)continue;const result=await page.evaluate(async id=>{const response=await fetch(`/api/v1/labs/${id}/validation-report/`,{cache:"no-store"});return response.ok?response.json():null},labId);if(result?.ready&&result.devices?.length===2&&result.devices.every(device=>device.template.name==="FRR Router")){workspaceHref=href;report=result;break}}
  if(!workspaceHref||!report)throw new Error("No deployment-ready two-router FRR draft was available");
  await page.goto(`${baseUrl}${workspaceHref}`,{waitUntil:"networkidle"});const editor=page.frameLocator('iframe[title^="Topology workspace"]');await editor.locator(".workspace-shell").waitFor();await editor.getByRole("button",{name:"Interop"}).click();
  const dialog=editor.getByRole("dialog",{name:"Containerlab interoperability"});await dialog.waitFor();const downloadPromise=page.waitForEvent("download");await dialog.getByRole("link",{name:"Download .clab.yml"}).click();const download=await downloadPromise;
  if(!download.suggestedFilename().endsWith(".clab.yml"))throw new Error("Containerlab export filename is not portable");const downloadedPath=await download.path();if(!downloadedPath)throw new Error("Firefox did not retain the downloaded topology");
  const exported=await fs.readFile(downloadedPath,"utf8");if(!exported.includes("topology:")||(exported.match(/\n\s+kind:/g)||[]).length!==2||(exported.match(/endpoints:/g)||[]).length!==1)throw new Error("Exported topology did not contain the saved BGP graph");
  await dialog.locator('input[type="file"]').setInputFiles(downloadedPath);await dialog.locator(".interop-review").waitFor();const rows=dialog.locator(".interop-mappings>article");if(await rows.count()!==2)throw new Error("Import preview did not render two device mappings");
  for(let index=0;index<2;index++){const row=rows.nth(index),template=row.locator("select").nth(0);const option=await template.locator("option").evaluateAll(options=>options.find(item=>item.textContent?.includes("FRR Router v1"))?.value||"");if(!option)throw new Error("FRR Router v1 was unavailable for an exported router");await template.selectOption(option);const image=row.locator("select").nth(1);if(!(await image.inputValue()))throw new Error("Exact immutable source image was not selected after template mapping");const selected=await image.locator("option:checked").innerText();if(!selected.includes("source match"))throw new Error("Import did not identify the exported immutable image")}
  const create=dialog.getByRole("button",{name:"Create mapped Studio draft"});if(!(await create.isEnabled()))throw new Error("Fully mapped safe topology did not become importable");const text=(await dialog.innerText()).toLowerCase();for(const phrase of ["device mappings","import impact","active runtime","remain unchanged","normal lab operation remains gui-only"]){if(!text.includes(phrase))throw new Error(`Interop workflow is missing: ${phrase}`)}
  await page.screenshot({path:output,fullPage:true});await dialog.getByRole("button",{name:"Cancel"}).click();
  console.log(JSON.stringify({workspaceHref,lab:report.lab,exportedFile:download.suggestedFilename(),nodes:2,links:1,mappedTemplate:"FRR Router v1",screenshot:output,mutation:"none"}));
}finally{await browser.close()}
