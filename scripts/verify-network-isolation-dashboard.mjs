import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require=createRequire(import.meta.url);const {firefox}=require("playwright");
const baseUrl=(process.env.TRAINING_BASE_URL||"").replace(/\/$/,"");
const username=process.env.TRAINING_USERNAME||"",password=process.env.TRAINING_PASSWORD||"";
const output=path.resolve(process.env.NETWORK_ISOLATION_SCREENSHOT||"training/71-platform-network-isolation.png");
if(!baseUrl||!username||!password)throw new Error("Set TRAINING_BASE_URL, TRAINING_USERNAME, and TRAINING_PASSWORD.");
const browser=await firefox.launch({headless:true});
const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1600,height:1100},colorScheme:"dark"});
const page=await context.newPage();
try{
  await page.goto(`${baseUrl}/accounts/login/`,{waitUntil:"networkidle"});
  await page.locator("#id_username").fill(username);await page.locator("#id_password").fill(password);
  await Promise.all([page.waitForURL(url=>!url.pathname.includes("/accounts/login/")),page.getByRole("button",{name:/sign in|log in/i}).click()]);
  const deadline=Date.now()+90000;let evidence="";
  while(Date.now()<deadline){
    await page.goto(`${baseUrl}/`,{waitUntil:"networkidle"});
    const row=page.locator(".service-list > div").filter({hasText:"Network isolation"});await row.waitFor();evidence=await row.innerText();
    if(evidence.includes("5/5 ingress policies verified")&&evidence.includes("Ready"))break;
    await page.waitForTimeout(3000);
  }
  if(!evidence.includes("5/5 ingress policies verified")||!evidence.includes("Ready"))throw new Error(`Network isolation evidence was not healthy: ${evidence}`);
  await page.screenshot({path:output,fullPage:true});
  console.log(JSON.stringify({browser:"Firefox",page:"Platform dashboard",networkIsolation:evidence.replace(/\n/g," · "),screenshot:output}));
}finally{await browser.close()}
