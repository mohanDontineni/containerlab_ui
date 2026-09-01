import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);
const { firefox } = require("playwright");
const baseUrl=(process.env.TRAINING_BASE_URL||"").replace(/\/$/,"");
const username=process.env.TRAINING_USERNAME||"",password=process.env.TRAINING_PASSWORD||"";
const deploymentId=process.env.METRICS_DEPLOYMENT_ID||"";
const output=path.resolve(process.env.METRICS_SCREENSHOT||"training/54-live-device-resource-telemetry.png");
if(!baseUrl||!username||!password||!deploymentId)throw new Error("Set the training URL, credentials, and METRICS_DEPLOYMENT_ID.");

const browser=await firefox.launch({headless:true});
const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1600,height:1100},colorScheme:"dark"});
const page=await context.newPage();const errors=[];
page.on("pageerror",error=>errors.push(error.message));
page.on("response",response=>{if(response.status()>=400)errors.push(`${response.status()} ${response.url()}`)});
try{
  await page.goto(`${baseUrl}/accounts/login/`,{waitUntil:"domcontentloaded"});
  await page.locator("#id_username").fill(username);await page.locator("#id_password").fill(password);
  await Promise.all([page.waitForURL(url=>!url.pathname.includes("/accounts/login/")),page.getByRole("button",{name:/sign in|log in/i}).click()]);
  await page.goto(`${baseUrl}/deployments/${deploymentId}/`,{waitUntil:"domcontentloaded"});
  await page.locator("#resource-telemetry").waitFor({state:"visible"});
  let runtime;
  for(let attempt=0;attempt<8;attempt+=1){
    runtime=await (await context.request.get(`${baseUrl}/api/v1/deployments/${deploymentId}/runtime/`,{failOnStatusCode:true})).json();
    if(runtime.devices.length===2&&runtime.devices.every(device=>device.runtime_resources?.telemetry?.available))break;
    await page.getByRole("button",{name:/refresh/i}).first().click();await page.waitForTimeout(5000);
  }
  if(runtime.devices.length!==2||runtime.devices.some(device=>!device.runtime_resources?.telemetry?.available))throw new Error("Two live device metric snapshots were not available.");
  await page.reload({waitUntil:"domcontentloaded"});await page.locator(".telemetry-card:not(.unavailable)").first().waitFor({state:"visible",timeout:15000});
  if(await page.locator(".telemetry-card:not(.unavailable)").count()!==2)throw new Error("Expected two live resource cards.");
  const text=await page.locator("#resource-telemetry").textContent();
  for(const device of runtime.devices)if(!text.includes(device.name)||!text.includes("CPU")||!text.includes("Memory"))throw new Error(`Telemetry UI omitted ${device.name}.`);
  await page.locator(".resource-telemetry-panel").screenshot({path:output});
  if(errors.length)throw new Error(`Firefox reported errors: ${errors.join(" | ")}`);
  console.log(JSON.stringify({browser:"firefox",devices:runtime.devices.map(device=>({name:device.name,...device.runtime_resources.telemetry})),screenshot:output}));
}finally{await browser.close()}
