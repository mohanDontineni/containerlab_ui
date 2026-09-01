import { Terminal } from '@xterm/xterm';
import '@xterm/xterm/css/xterm.css';
import './console.css';
import { requestedConsoleDevice,visibleConsoleIds } from './console-utils';

type Device={id:string;node_id:string;name:string;kind:string;observed_readiness:string};
type ConsolePane={device:Device;terminal:Terminal;socket:WebSocket|null;sessionId:string;status:string;readOnly:boolean};
const deployment=new URLSearchParams(location.search).get('deployment')||'';
let requestedDevice=new URLSearchParams(location.search).get('device')||'';
const root=document.getElementById('console-app')!;
const csrf=()=>document.cookie.split('; ').find(x=>x.startsWith('csrftoken='))?.split('=')[1]||'';
root.innerHTML='<header><div><strong>Multi-device console workspace</strong><small>Authenticated sessions · 15-minute idle expiry · up to two visible panes</small></div><div class="console-layout"><button id="single-layout" class="active">▣ Single</button><button id="split-layout">▥ Split</button><span id="connection-state">No sessions</span></div></header><nav id="device-tabs"></nav><section id="open-console-tabs"></section><main id="terminal-host"><div class="console-empty">Choose a ready device to open its console.</div></main>';
let devices:Device[]=[],activeId='',split=false;
const panes=new Map<string,ConsolePane>();

function render(){
  document.getElementById('device-tabs')!.innerHTML=devices.map(device=>`<button data-device="${device.id}" ${device.observed_readiness!=='ready'?'disabled':''}><i></i>${device.name}<small>${panes.has(device.id)?'open':device.observed_readiness}</small></button>`).join('')||'<span>No runtime devices discovered</span>';
  document.querySelectorAll<HTMLButtonElement>('[data-device]').forEach(button=>button.onclick=()=>openConsole(button.dataset.device!));
  const tabs=document.getElementById('open-console-tabs')!;tabs.innerHTML=[...panes.values()].map(pane=>`<button data-console-tab="${pane.device.id}" class="${pane.device.id===activeId?'active':''}"><i class="state-${pane.status}"></i><span>${pane.device.name}</span><small>${pane.readOnly?'read only':pane.status}</small><b data-close-console="${pane.device.id}" title="Close and revoke ${pane.device.name}">×</b></button>`).join('');
  tabs.querySelectorAll<HTMLButtonElement>('[data-console-tab]').forEach(button=>button.onclick=event=>{if((event.target as HTMLElement).closest('[data-close-console]'))return;activeId=button.dataset.consoleTab!;render()});
  tabs.querySelectorAll<HTMLElement>('[data-close-console]').forEach(button=>button.onclick=event=>{event.stopPropagation();closeConsole(button.dataset.closeConsole!)});
  const visible=visibleConsoleIds([...panes.keys()],activeId,split),host=document.getElementById('terminal-host')!;
  host.classList.toggle('split',visible.length===2);host.innerHTML=visible.length?'':'<div class="console-empty">Choose a ready device to open its console.</div>';
  for(const id of visible){const pane=panes.get(id)!;const shell=document.createElement('article');shell.className='console-pane';shell.innerHTML=`<div class="console-pane-heading"><strong>${pane.device.name}</strong><span>${pane.readOnly?'Viewer · read only':pane.status}</span><button data-reconnect="${id}">↻ Reconnect</button></div><div class="terminal-mount"></div>`;host.append(shell);const mount=shell.querySelector<HTMLElement>('.terminal-mount')!;if(pane.terminal.element)mount.append(pane.terminal.element);else pane.terminal.open(mount);pane.terminal.refresh(0,pane.terminal.rows-1);shell.querySelector<HTMLButtonElement>('[data-reconnect]')!.onclick=()=>reconnect(id)}
  document.getElementById('single-layout')!.classList.toggle('active',!split);document.getElementById('split-layout')!.classList.toggle('active',split);
  document.getElementById('connection-state')!.textContent=panes.size?`${panes.size} session${panes.size===1?'':'s'} open`:'No sessions';
}
async function authorize(device:Device,terminal:Terminal){
  const response=await fetch(`/api/v1/deployments/${deployment}/consoles/`,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':csrf()},body:JSON.stringify({device_id:device.id})}),session=await response.json();
  if(!response.ok)throw new Error(session.error?.details||session.error?.code||'Console authorization failed');
  const pane=panes.get(device.id)!;pane.sessionId=session.id;pane.readOnly=session.read_only;pane.status='connecting';
  const scheme=location.protocol==='https:'?'wss':'ws',socket=new WebSocket(`${scheme}://${location.host}${session.websocket}`);pane.socket=socket;
  socket.onmessage=event=>{const message=JSON.parse(event.data);if(message.type==='output')terminal.write(message.data);if(message.type==='status'){pane.status=message.state;terminal.writeln(`\r\n\x1b[90m[${message.state}]\x1b[0m`);render()}if(message.type==='error')terminal.writeln(`\r\n\x1b[31m${message.message}\x1b[0m`)};
  socket.onclose=()=>{if(panes.has(device.id)&&pane.socket===socket){pane.status='disconnected';render()}};
  terminal.onData(data=>{if(socket.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:'input',data}))});
  setTimeout(()=>{if(socket.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:'resize',columns:terminal.cols,rows:terminal.rows}))},500);
}
async function openConsole(deviceId:string){
  const device=devices.find(item=>item.id===deviceId&&item.observed_readiness==='ready');if(!device)return;
  if(panes.has(deviceId)){activeId=deviceId;render();return}
  const terminal=new Terminal({cursorBlink:true,fontFamily:'JetBrains Mono, monospace',fontSize:12,theme:{background:'#050c14',foreground:'#d7e7ef',cursor:'#26c6da',selectionBackground:'#244863'},scrollback:5000});
  panes.set(deviceId,{device,terminal,socket:null,sessionId:'',status:'authorizing',readOnly:false});activeId=deviceId;render();terminal.writeln(`\x1b[36mConnecting to ${device.name}…\x1b[0m`);
  try{await authorize(device,terminal)}catch(error){const pane=panes.get(deviceId);if(pane)pane.status='failed';terminal.writeln(`\x1b[31m${error instanceof Error?error.message:'Console authorization failed'}\x1b[0m`);render()}
}
async function revoke(pane:ConsolePane){if(!pane.sessionId)return;await fetch(`/api/v1/deployments/${deployment}/consoles/${pane.sessionId}/`,{method:'DELETE',headers:{'X-CSRFToken':csrf()}}).catch(()=>{})}
async function closeConsole(deviceId:string){const pane=panes.get(deviceId);if(!pane)return;pane.socket?.close();await revoke(pane);pane.terminal.dispose();panes.delete(deviceId);if(activeId===deviceId)activeId=[...panes.keys()].at(-1)||'';render()}
async function reconnect(deviceId:string){const pane=panes.get(deviceId);if(!pane)return;pane.socket?.close();await revoke(pane);pane.terminal.clear();pane.terminal.writeln(`\x1b[36mReconnecting to ${pane.device.name}…\x1b[0m`);pane.sessionId='';pane.status='authorizing';render();try{await authorize(pane.device,pane.terminal)}catch(error){pane.status='failed';pane.terminal.writeln(`\x1b[31m${error instanceof Error?error.message:'Reconnect failed'}\x1b[0m`);render()}}
async function load(){const response=await fetch(`/api/v1/deployments/${deployment}/runtime/`);if(!response.ok)throw new Error('Unable to load runtime devices');devices=(await response.json()).devices;render();const requested=devices.find(device=>device.id===requestedDevice&&device.observed_readiness==='ready');if(requested){requestedDevice='';await openConsole(requested.id)}}
document.getElementById('single-layout')!.onclick=()=>{split=false;render()};document.getElementById('split-layout')!.onclick=()=>{split=true;render()};
window.addEventListener('message',event=>{const deviceId=requestedConsoleDevice(event.origin,location.origin,event.data);if(deviceId)openConsole(deviceId)});
window.addEventListener('beforeunload',()=>panes.forEach(pane=>pane.socket?.close()));
load().catch(error=>root.innerHTML=`<div class="console-error">${error.message}</div>`);
