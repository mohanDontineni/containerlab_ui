import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require=createRequire(import.meta.url);const {firefox}=require("playwright");
const baseUrl=(process.env.TRAINING_BASE_URL||"").replace(/\/$/,"");
const username=process.env.TRAINING_USERNAME||"",password=process.env.TRAINING_PASSWORD||"";
const output=path.resolve(process.env.REGISTRY_MIRROR_SCREENSHOT||"training/70-verified-image-registry-mirror.png");
if(!baseUrl||!username||!password)throw new Error("Set TRAINING_BASE_URL, TRAINING_USERNAME, and TRAINING_PASSWORD.");
const browser=await firefox.launch({headless:true});
const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1600,height:1100},colorScheme:"dark"});
const page=await context.newPage();
try{
  await page.goto(`${baseUrl}/accounts/login/`,{waitUntil:"networkidle"});
  await page.locator("#id_username").fill(username);await page.locator("#id_password").fill(password);
  await Promise.all([page.waitForURL(url=>!url.pathname.includes("/accounts/login/")),page.getByRole("button",{name:/sign in|log in/i}).click()]);
  await page.goto(`${baseUrl}/images/`,{waitUntil:"networkidle"});
  const repair=page.locator('[data-publish-image][data-force="true"]').first();
  await repair.waitFor();const artifactId=await repair.getAttribute("data-publish-image");
  if(!artifactId)throw new Error("No repairable validated upload was available");
  let evidence=await page.evaluate(async id=>{const response=await fetch(`/api/v1/images/${id}/evidence/`,{cache:"no-store"});return response.ok?response.json():null},artifactId);
  if(!evidence?.publications?.some(row=>row.compatibility?.registry_mirror?.verified))await repair.click();
  const deadline=Date.now()+240000;
  while(Date.now()<deadline){
    await page.waitForTimeout(3000);
    try{await page.waitForLoadState("domcontentloaded",{timeout:5000});evidence=await page.evaluate(async id=>{const response=await fetch(`/api/v1/images/${id}/evidence/`,{cache:"no-store"});return response.ok?response.json():null},artifactId)}catch{continue}
    const mirror=evidence?.publications?.find(row=>row.compatibility?.registry_mirror?.verified)?.compatibility?.registry_mirror;
    const build=evidence?.builds?.find(row=>row.recipe_version==="node-containerd-registry-v2");
    if(mirror&&build?.status==="succeeded")break;
    if(build?.status==="failed")throw new Error(`Registry mirror publication failed: ${JSON.stringify(build.failure||{})}`);
  }
  const publication=evidence?.publications?.find(row=>row.compatibility?.registry_mirror?.verified);
  if(!publication)throw new Error("Registry mirror was not verified before timeout");
  await page.reload({waitUntil:"networkidle"});
  await page.locator(`[data-image-evidence="${artifactId}"]`).click();
  const dialog=page.locator("#image-evidence-dialog");await dialog.waitFor();
  await dialog.getByText("node-containerd+internal-registry",{exact:false}).waitFor({timeout:30000});
  const text=await dialog.innerText();
  for(const phrase of ["node-containerd+internal-registry","Registry reference","Manifest digest","Registry manifest verified"]){if(!text.includes(phrase))throw new Error(`Image evidence is missing ${phrase}`)}
  await page.screenshot({path:output,fullPage:true});
  console.log(JSON.stringify({browser:"Firefox",artifactId,image:evidence.name,publicationMode:publication.compatibility.publication_mode,
    registryMirror:publication.compatibility.registry_mirror,screenshot:output}));
}finally{await browser.close()}
