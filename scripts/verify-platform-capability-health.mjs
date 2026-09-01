import {createRequire} from "node:module";
import path from "node:path";
import process from "node:process";
const require=createRequire(import.meta.url);const {firefox}=require("playwright");
const baseUrl=(process.env.TRAINING_BASE_URL||"").replace(/\/$/,""),username=process.env.TRAINING_USERNAME||"",password=process.env.TRAINING_PASSWORD||"";
const deploymentId=process.env.HEALTH_DEPLOYMENT_ID||"",output=path.resolve(process.env.HEALTH_SCREENSHOT||"training/55-verified-platform-capabilities.png");
if(!baseUrl||!username||!password||!deploymentId)throw new Error("Set training URL, credentials, and HEALTH_DEPLOYMENT_ID.");
const browser=await firefox.launch({headless:true});const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1600,height:1100},colorScheme:"dark"});
const page=await context.newPage(),errors=[];page.on("pageerror",error=>errors.push(error.message));page.on("response",response=>{if(response.status()>=400)errors.push(`${response.status()} ${response.url()}`)});
try{
  await page.goto(`${baseUrl}/accounts/login/`,{waitUntil:"domcontentloaded"});await page.locator("#id_username").fill(username);await page.locator("#id_password").fill(password);
  await Promise.all([page.waitForURL(url=>!url.pathname.includes("/accounts/login/")),page.getByRole("button",{name:/sign in|log in/i}).click()]);
  await page.goto(`${baseUrl}/deployments/${deploymentId}/`,{waitUntil:"domcontentloaded"});await page.getByRole("button",{name:/refresh/i}).first().click();
  let ready=false;
  for(let attempt=0;attempt<12;attempt+=1){await page.waitForTimeout(2500);await page.goto(`${baseUrl}/`,{waitUntil:"domcontentloaded"});const panel=page.locator(".health-panel");const text=await panel.textContent();if(text.includes("Resource metrics")&&text.includes("Metrics API · worker verified")&&text.includes("Runtime v0.8.0 · reconciled")&&(await panel.locator("b.healthy",{hasText:"Ready"}).count())===4){ready=true;break}}
  if(!ready)throw new Error("Worker-verified platform capabilities did not become ready.");
  await page.locator(".health-panel").screenshot({path:output});if(errors.length)throw new Error(`Firefox reported errors: ${errors.join(" | ")}`);
  console.log(JSON.stringify({browser:"firefox",database:"ready",jobs:"ready",runtime:"0.8.0 ready",metrics:"worker verified ready",screenshot:output}));
}finally{await browser.close()}
