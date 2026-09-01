import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require=createRequire(import.meta.url);const {firefox}=require("playwright");
const baseUrl=(process.env.TRAINING_BASE_URL||"").replace(/\/$/,"");
const username=process.env.TRAINING_USERNAME||"",password=process.env.TRAINING_PASSWORD||"";
const output=path.resolve(process.env.OCI_REGISTRY_SCREENSHOT||"training/69-kubernetes-oci-registry.png");
if(!baseUrl||!username||!password)throw new Error("Set TRAINING_BASE_URL, TRAINING_USERNAME, and TRAINING_PASSWORD.");
const browser=await firefox.launch({headless:true});
const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1600,height:1100},colorScheme:"dark"});
const page=await context.newPage();
try{
  await page.goto(`${baseUrl}/accounts/login/`,{waitUntil:"networkidle"});
  await page.locator("#id_username").fill(username);await page.locator("#id_password").fill(password);
  await Promise.all([page.waitForURL(url=>!url.pathname.includes("/accounts/login/")),page.getByRole("button",{name:/sign in|log in/i}).click()]);
  await page.goto(`${baseUrl}/`,{waitUntil:"networkidle"});
  const registry=page.locator(".service-list > div").filter({hasText:"OCI registry"});
  await registry.waitFor();
  const evidence=await registry.innerText();
  if(!evidence.includes("Persistent filesystem · internal ClusterIP")||!evidence.includes("Ready"))throw new Error(`Registry dashboard evidence was not healthy: ${evidence}`);
  if(!(await page.getByText("Clabernetes",{exact:true}).isVisible()))throw new Error("Runtime health was not visible");
  await page.screenshot({path:output,fullPage:true});
  console.log(JSON.stringify({browser:"Firefox",page:"Platform dashboard",registry:evidence.replace(/\n/g," · "),screenshot:output}));
}finally{await browser.close()}
