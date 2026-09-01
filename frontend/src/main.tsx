import React, {
  DragEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createRoot } from "react-dom/client";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  Panel,
  Handle,
  Position,
  Connection,
  Edge,
  EdgeChange,
  Node,
  NodeChange,
  applyEdgeChanges,
  applyNodeChanges,
  addEdge,
  MarkerType,
  ViewportPortal,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./style.css";
import "./clone.css";
import "./configuration.css";
import "./annotations.css";
import "./bulk-selection.css";
import "./edit-lease.css";
import { alignSelectedNodes, arrangeTopology, duplicateSubgraph, interfaceFromHandle } from "./topology-utils";

type Template = {
  id: string;
  name: string;
  kind: string;
  category: string;
  icon: string;
  verified: boolean;
  privileged: boolean;
  interfaces: string[];
  managementInterface: string;
  resources: Record<string, string>;
  capabilities: Record<string, boolean>;
  configurationLanguage: string;
  startupConfigSupported: boolean;
  startupConfigRequired: boolean;
  requiredInterfaces: number;
};
type DeviceData = {
  label: string;
  templateId: string;
  kind: string;
  interfaces: string[];
  verified: boolean;
  status: string;
  imageId: string;
  startupConfig: string;
  icon: string;
  configurationLanguage: string;
  startupConfigSupported: boolean;
  startupConfigRequired: boolean;
  requiredInterfaces: number;
  startupOrder: number | null;
  [key: string]: unknown;
};
type SavedNode = {
  id: string;
  name: string;
  templateVersionId: string;
  publishedImageId: string | null;
  position: { x: number; y: number };
  properties: Record<string, unknown>;
  startupConfiguration: string;
  interfaces: { name: string }[];
};
type SavedLink = {
  id: string;
  sourceNode: string;
  sourceInterface: string;
  targetNode: string;
  targetInterface: string;
  label: string;
  properties: Record<string, unknown>;
};
type TopologyAnnotation = { id:string;type:"note"|"region";x:number;y:number;width:number;height:number;
  text:string;color:"cyan"|"blue"|"violet"|"amber"|"rose"|"green"|"slate";fontSize:number;zIndex:number };
type Snapshot = { nodes: Node<DeviceData>[]; edges: Edge[]; annotations:TopologyAnnotation[] };
type PublishedImage = { id: string; name: string; digest: string; architecture: string; status: string };
type RevisionSummary = { id:string; revision_number:number; edit_version:number; immutable:boolean; topology_checksum:string;
  node_count:number; link_count:number; deployment_count:number; created_at:string; is_current_draft:boolean };
type RevisionComparison = {left:{id:string;revision_number:number;checksum:string};right:{id:string;revision_number:number;checksum:string};
  summary:{nodes_added:number;nodes_removed:number;nodes_modified:number;links_added:number;links_removed:number;links_modified:number;annotations_changed:number;canvas_changed:boolean};
  nodes:{added:string[];removed:string[];modified:{name:string;fields:{field:string;before:unknown;after:unknown}[]}[]};
  links:{added:string[];removed:string[];modified:{link:string;fields:string[]}[]};annotations:{added:number;removed:number;modified:number}};
type BundlePreview = { checksum:string; source_lab:string; destination_lab:string; node_count:number; link_count:number;
  configured_node_count:number; template_count:number; image_count:number; templates:string[]; will_replace_draft:boolean;
  preserved_published_revisions:number; running_deployments_unchanged:number; expected_current_draft:string|null;
  deployable:boolean; deployability_issues:string[] };
type EditLease = {active:boolean;can_edit:boolean;owner:string|null;expires_at:string|null;lease_seconds:number;token?:string};
const params = new URLSearchParams(location.search);
const labId = params.get("lab") || "";
const labName = params.get("name") || "Topology Workspace";
const csrf = () =>
  document.cookie
    .split("; ")
    .find((x) => x.startsWith("csrftoken="))
    ?.split("=")[1] || "";
const ifaceFromHandle = interfaceFromHandle;
const iconFor = (icon: string) =>
  ({ router: "↔", switch: "▦", firewall: "◆", host: "□" })[icon] || "◇";

function DeviceNode({
  data,
  selected,
}: {
  id: string;
  data: DeviceData;
  selected?: boolean;
}) {
  return (
    <div
      className={`topology-device ${selected ? "selected" : ""}`}
      style={{ minHeight: Math.max(112, 48 + data.interfaces.length * 15) }}
    >
      <div className="device-state">
        <i className={`dot ${data.status}`}></i>
        {data.status}
      </div>
      <div className="device-glyph">
        {iconFor(data.icon)}
      </div>
      <strong>{data.label}</strong>
      <small>{data.kind}</small>
      {data.interfaces.map((iface, index) => {
        const top = `${58 + index * 15}px`;
        return (
          <React.Fragment key={iface}>
            <Handle
              type="target"
              position={Position.Left}
              id={`t:${iface}`}
              style={{ top }}
              className="iface-handle"
            />
            <Handle
              type="source"
              position={Position.Right}
              id={`s:${iface}`}
              style={{ top }}
              className="iface-handle"
            />
            <span className="iface-label left" style={{ top }}>
              {iface}
            </span>
            <span className="iface-label right" style={{ top }}>
              {iface}
            </span>
          </React.Fragment>
        );
      })}
    </div>
  );
}
const nodeTypes = { device: DeviceNode };

function Workspace() {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [images, setImages] = useState<PublishedImage[]>([]);
  const [nodes, setNodes] = useState<Node<DeviceData>[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [annotations,setAnnotations]=useState<TopologyAnnotation[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [editVersion, setEditVersion] = useState(0);
  const [saving, setSaving] = useState(false);
  const [deploying, setDeploying] = useState(false);
  const [cloneOpen, setCloneOpen] = useState(false);
  const [cloneName, setCloneName] = useState(`${labName} copy`);
  const [cloning, setCloning] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [restoring, setRestoring] = useState<string | null>(null);
  const [bundleRestore, setBundleRestore] = useState<{file:File;preview:BundlePreview}|null>(null);
  const [bundleRestoring, setBundleRestoring] = useState(false);
  const [revisions, setRevisions] = useState<RevisionSummary[]>([]);
  const [selectedRevisionIds,setSelectedRevisionIds]=useState<string[]>([]);
  const [revisionComparison,setRevisionComparison]=useState<RevisionComparison|null>(null);
  const [comparingRevisions,setComparingRevisions]=useState(false);
  const [currentDraftId, setCurrentDraftId] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [workspaceReady,setWorkspaceReady]=useState(false);
  const [editLease,setEditLease]=useState<EditLease|null>(null);
  const leaseToken=useRef("");
  const [templateQuery,setTemplateQuery]=useState("");
  const [notice, setNotice] = useState("Ready");
  const [history, setHistory] = useState<Snapshot[]>([]);
  const [future, setFuture] = useState<Snapshot[]>([]);
  const [rf, setRf] = useState<any>(null);
  const counter = useRef(1);
  const importInput = useRef<HTMLInputElement>(null);
  const snapshot = useCallback(() => {
    setHistory((h) => [
      ...h.slice(-29),
      { nodes: structuredClone(nodes), edges: structuredClone(edges),annotations:structuredClone(annotations) },
    ]);
    setFuture([]);
  }, [nodes, edges,annotations]);
  useEffect(() => {
    Promise.all([
      fetch("/api/v1/topology/templates/").then((r) => r.json()),
      fetch(`/api/v1/labs/${labId}/topology/images/`).then((r) => r.json()),
      fetch(`/api/v1/labs/${labId}/topology/`).then((r) => r.json()),
      fetch(`/api/v1/labs/${labId}/topology/edit-lease/`,{method:"POST",credentials:"same-origin",headers:{"X-CSRFToken":csrf()}}).then(async r=>({ok:r.ok,data:await r.json()})),
    ])
      .then(([catalog, imageCatalog, doc, leaseResult]) => {
        setTemplates(catalog.templates);
        setImages(imageCatalog.images);
        const map = new Map<string, Template>(
          catalog.templates.map((t: Template) => [t.id, t]),
        );
        setNodes(
          doc.nodes.map((n: SavedNode) => {
            const t = map.get(n.templateVersionId);
            return {
              id: n.id,
              type: "device",
              position: n.position,
              data: {
                label: n.name,
                templateId: n.templateVersionId,
                kind: t?.kind || "device",
                interfaces: n.interfaces.map((i) => i.name),
                verified: t?.verified || false,
                status: "stopped",
                imageId: n.publishedImageId || "",
                startupConfig: n.startupConfiguration || "",
                icon: t?.icon || "device",
                configurationLanguage: t?.configurationLanguage || "text",
                startupConfigSupported: t?.startupConfigSupported || false,
                startupConfigRequired: t?.startupConfigRequired || false,
                requiredInterfaces: t?.requiredInterfaces || 0,
                startupOrder: Number.isInteger(n.properties?.startupOrder) ? Number(n.properties.startupOrder) : null,
              },
            };
          }),
        );
        setEdges(
          doc.links.map((l: SavedLink) => ({
            id: l.id,
            source: l.sourceNode,
            target: l.targetNode,
            sourceHandle: `s:${l.sourceInterface}`,
            targetHandle: `t:${l.targetInterface}`,
            label: l.label || `${l.sourceInterface} ↔ ${l.targetInterface}`,
            type: "smoothstep",
            markerEnd: { type: MarkerType.ArrowClosed },
            data: { properties: l.properties },
          })),
        );
        setAnnotations(Array.isArray(doc.annotations)?doc.annotations:[]);
        setEditVersion(doc.editVersion);
        counter.current = doc.nodes.length + 1;
        setWorkspaceReady(true);
        const lease=leaseResult.data.error?leaseResult.data.error:leaseResult.data;
        if(leaseResult.ok&&lease.token){leaseToken.current=lease.token;setEditLease(lease);setNotice("Draft loaded · editing session secured");}
        else {setEditLease(lease);setNotice(`${lease.owner||"Another operator"} is editing · read-only mode`);}
      })
      .catch(() => setNotice("Unable to load workspace"));
  }, []);
  useEffect(()=>{
    if(!editLease?.can_edit||!leaseToken.current)return;
    const renew=window.setInterval(async()=>{const response=await fetch(`/api/v1/labs/${labId}/topology/edit-lease/`,{method:"POST",credentials:"same-origin",headers:{"X-CSRFToken":csrf(),"X-Edit-Lease":leaseToken.current}});const data=await response.json();if(response.ok)setEditLease(data);else{leaseToken.current="";setEditLease(data.error||data);setNotice("Editing session lost · workspace is now read-only")}},120000);
    const release=()=>{if(leaseToken.current)fetch(`/api/v1/labs/${labId}/topology/edit-lease/`,{method:"DELETE",credentials:"same-origin",keepalive:true,headers:{"X-CSRFToken":csrf(),"X-Edit-Lease":leaseToken.current}}).catch(()=>{})};
    addEventListener("pagehide",release);return()=>{clearInterval(renew);removeEventListener("pagehide",release)};
  },[editLease?.can_edit]);
  const canEdit=Boolean(editLease?.can_edit);
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    addEventListener("beforeunload", warn);
    return () => removeEventListener("beforeunload", warn);
  }, [dirty]);
  const used = useMemo(
    () =>
      new Set(
        edges.flatMap((e) => [
          `${e.source}:${ifaceFromHandle(e.sourceHandle)}`,
          `${e.target}:${ifaceFromHandle(e.targetHandle)}`,
        ]),
      ),
    [edges],
  );
  const selectedNode = nodes.find((n) => n.id === selected);
  const selectedEdge = edges.find((e) => e.id === selected);
  const selectedAnnotation=annotations.find((annotation)=>`annotation:${annotation.id}`===selected);
  const selectedDeviceNodes=nodes.filter(node=>node.selected);
  const filteredTemplates=templates.filter(template=>`${template.name} ${template.kind} ${template.category}`.toLowerCase().includes(templateQuery.trim().toLowerCase()));
  const onNodesChange = useCallback(
    (changes: NodeChange<Node<DeviceData>>[]) => {
      if(!canEdit)return;
      setNodes((n) => applyNodeChanges(changes, n));
      if (
        changes.some((change) =>
          ["add", "remove", "replace", "position"].includes(change.type),
        )
      )
        setDirty(true);
    },
    [canEdit],
  );
  const onEdgesChange = useCallback(
    (changes: EdgeChange<Edge>[]) => {
      if(!canEdit)return;
      if (changes.some((c) => c.type === "remove")) snapshot();
      setEdges((e) => applyEdgeChanges(changes, e));
      if (
        changes.some((change) =>
          ["add", "remove", "replace"].includes(change.type),
        )
      )
        setDirty(true);
    },
    [snapshot,canEdit],
  );
  const validConnection = useCallback(
    (c: Connection | Edge) => {
      const si = ifaceFromHandle(c.sourceHandle),
        ti = ifaceFromHandle(c.targetHandle);
      return Boolean(
        c.source &&
          c.target &&
          c.source !== c.target &&
          si &&
          ti &&
          !used.has(`${c.source}:${si}`) &&
          !used.has(`${c.target}:${ti}`),
      );
    },
    [used],
  );
  const onConnect = useCallback(
    (c: Connection) => {
      if(!canEdit)return;
      if (!validConnection(c)) {
        setNotice("Connection rejected: choose two unused interfaces");
        return;
      }
      snapshot();
      const si = ifaceFromHandle(c.sourceHandle),
        ti = ifaceFromHandle(c.targetHandle);
      setEdges((es) =>
        addEdge(
          {
            ...c,
            id: crypto.randomUUID(),
            label: `${si} ↔ ${ti}`,
            type: "smoothstep",
            animated: false,
            markerEnd: { type: MarkerType.ArrowClosed },
          },
          es,
        ),
      );
      setDirty(true);
      setNotice(`Connected ${si} to ${ti}`);
    },
    [snapshot, validConnection,canEdit],
  );
  const drop = useCallback(
    (event: DragEvent) => {
      event.preventDefault();
      const id = event.dataTransfer.getData("template");
      const template = templates.find((t) => t.id === id);
      if (!template || !rf || !workspaceReady || !canEdit) return;
      snapshot();
      const position = rf.screenToFlowPosition({
        x: event.clientX,
        y: event.clientY,
      });
      const base =
        template.name
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, "-")
          .replace(/^-|-$/g, "")
          .slice(0, 30) || "node";
      let name = `${base}-${counter.current++}`;
      while (nodes.some((n) => n.data.label === name))
        name = `${base}-${counter.current++}`;
      const node: Node<DeviceData> = {
        id: crypto.randomUUID(),
        type: "device",
        position,
        data: {
          label: name,
          templateId: template.id,
          kind: template.kind,
          interfaces: template.interfaces,
          verified: template.verified,
          status: "stopped",
          imageId: "",
          startupConfig: "",
          icon: template.icon,
          configurationLanguage: template.configurationLanguage,
          startupConfigSupported: template.startupConfigSupported,
          startupConfigRequired: template.startupConfigRequired,
          requiredInterfaces: template.requiredInterfaces,
          startupOrder: null,
        },
      };
      setNodes((n) => [...n, node]);
      setSelected(node.id);
      setDirty(true);
      setNotice(`${template.name} added`);
    },
    [templates, rf, nodes, snapshot,workspaceReady,canEdit],
  );
  const addAnnotation=(type:"note"|"region")=>{
    if(!rf||!workspaceReady||!canEdit)return;snapshot();const center=rf.screenToFlowPosition({x:window.innerWidth/2,y:window.innerHeight/2});
    const annotation:TopologyAnnotation={id:crypto.randomUUID(),type,x:center.x-(type==="region"?180:120),y:center.y-(type==="region"?100:45),
      width:type==="region"?360:240,height:type==="region"?200:90,text:type==="region"?"Network zone":"Add an operator note",
      color:type==="region"?"blue":"amber",fontSize:type==="region"?16:14,zIndex:type==="region"?-10:10};
    setAnnotations(items=>[...items,annotation]);setSelected(`annotation:${annotation.id}`);setDirty(true);setNotice(type==="region"?"Region added":"Note added");
  };
  const updateAnnotation=(id:string,changes:Partial<TopologyAnnotation>)=>{
    if(!canEdit)return;
    setAnnotations(items=>items.map(item=>item.id===id?{...item,...changes}:item));setDirty(true);
  };
  const beginAnnotationGesture=(event:React.PointerEvent,annotation:TopologyAnnotation,resize=false)=>{
    if(!rf||!canEdit)return;event.preventDefault();event.stopPropagation();snapshot();setSelected(`annotation:${annotation.id}`);
    const startX=event.clientX,startY=event.clientY,zoom=rf.getZoom(),origin={x:annotation.x,y:annotation.y,width:annotation.width,height:annotation.height};
    const move=(next:PointerEvent)=>{const dx=(next.clientX-startX)/zoom,dy=(next.clientY-startY)/zoom;
      if(resize)updateAnnotation(annotation.id,{width:Math.max(80,Math.min(2000,origin.width+dx)),height:Math.max(40,Math.min(1600,origin.height+dy))});
      else updateAnnotation(annotation.id,{x:Math.max(-10000,Math.min(10000,origin.x+dx)),y:Math.max(-10000,Math.min(10000,origin.y+dy))});};
    const stop=()=>{window.removeEventListener("pointermove",move);window.removeEventListener("pointerup",stop)};
    window.addEventListener("pointermove",move);window.addEventListener("pointerup",stop,{once:true});
  };
  const save = async () => {
    if(!canEdit){setNotice("Read-only: another operator owns the editing session");return;}
    setSaving(true);
    setNotice("Saving draft…");
    const body = {
      editVersion,
      nodes: nodes.map((n) => ({
        id: n.id,
        name: n.data.label,
        templateVersionId: n.data.templateId,
        publishedImageId: n.data.imageId || null,
        position: n.position,
        properties: { kind: n.data.kind, ...(Number.isInteger(n.data.startupOrder)?{startupOrder:n.data.startupOrder}:{}) },
        startupConfiguration: n.data.startupConfig || "",
      })),
      links: edges.map((e) => ({
        id: e.id,
        sourceNode: e.source,
        sourceInterface: ifaceFromHandle(e.sourceHandle),
        targetNode: e.target,
        targetInterface: ifaceFromHandle(e.targetHandle),
        label: typeof e.label === "string" ? e.label : "",
        properties: e.data?.properties || {},
      })),
      annotations,
    };
    try {
      const r = await fetch(`/api/v1/labs/${labId}/topology/`, {
        method: "PUT",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf(), "X-Edit-Lease":leaseToken.current },
        body: JSON.stringify(body),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Save failed");
      setEditVersion(data.editVersion);
      setDirty(false);
      setNotice("All changes saved");
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };
  const updateImage = (imageId: string) => {
    if (!selectedNode||!canEdit) return;
    setNodes((ns) => ns.map((n) => n.id === selectedNode.id ? { ...n, data: { ...n.data, imageId } } : n));
    setDirty(true);
  };
  const exportBundle = () => {
    if (dirty) { setNotice("Save the draft before downloading a backup"); return; }
    location.href = `/api/v1/labs/${labId}/export/`;
  };
  const cloneLab = async () => {
    const name=cloneName.trim();
    if (!name) { setNotice("Enter a name for the copied lab"); return; }
    if (dirty) { setNotice("Save the draft before creating a copy"); setCloneOpen(false); return; }
    setCloning(true); setNotice("Creating an independent lab copy…");
    try {
      const response=await fetch(`/api/v1/labs/${labId}/clone/`,{method:"POST",credentials:"same-origin",
        headers:{"Content-Type":"application/json","X-CSRFToken":csrf()},body:JSON.stringify({name})});
      const data=await response.json();
      if (!response.ok) throw new Error(data.error?.details || data.error?.code || "Copy failed");
      setNotice(`Created ${data.node_count}-device copy`);
      location.href=data.workspace_url;
    } catch (error) { setNotice(error instanceof Error ? error.message : "Copy failed"); setCloning(false); }
  };
  const openHistory = async () => {
    if (dirty) { setNotice("Save the draft before opening revision history"); return; }
    setHistoryOpen(true);setHistoryLoading(true);setSelectedRevisionIds([]);setRevisionComparison(null);
    try {
      const response=await fetch(`/api/v1/labs/${labId}/revisions/`,{credentials:"same-origin"});const data=await response.json();
      if (!response.ok) throw new Error(data.error?.details||"Unable to load revision history");
      setRevisions(data.revisions);setCurrentDraftId(data.current_draft);
    } catch (error) { setNotice(error instanceof Error?error.message:"Unable to load revision history");setHistoryOpen(false); }
    finally { setHistoryLoading(false); }
  };
  const compareSelectedRevisions=async()=>{
    if(selectedRevisionIds.length!==2)return;
    const selected=revisions.filter(revision=>selectedRevisionIds.includes(revision.id)).sort((a,b)=>a.revision_number-b.revision_number);
    setComparingRevisions(true);setNotice(`Comparing revisions ${selected[0].revision_number} and ${selected[1].revision_number}…`);
    try{const response=await fetch(`/api/v1/labs/${labId}/revisions/compare/?left=${selected[0].id}&right=${selected[1].id}`,{credentials:"same-origin",cache:"no-store"}),data=await response.json();
      if(!response.ok)throw new Error(data.error?.details||data.error?.code||"Comparison unavailable");setRevisionComparison(data);setNotice(`Compared revision ${data.left.revision_number} → ${data.right.revision_number}`)
    }catch(error){setNotice(error instanceof Error?error.message:"Comparison unavailable")}finally{setComparingRevisions(false)}
  };
  const restoreRevision = async (revision:RevisionSummary) => {
    if (!confirm(`Restore revision ${revision.revision_number} as a new editable draft? Your current saved draft will be replaced.`)) return;
    setRestoring(revision.id);setNotice(`Restoring revision ${revision.revision_number}…`);
    try {
      const response=await fetch(`/api/v1/labs/${labId}/revisions/${revision.id}/restore/`,{method:"POST",credentials:"same-origin",
        headers:{"Content-Type":"application/json","X-CSRFToken":csrf(),"Idempotency-Key":crypto.randomUUID(),"X-Edit-Lease":leaseToken.current},
        body:JSON.stringify({expected_current_draft:currentDraftId})});const data=await response.json();
      if (!response.ok) throw new Error(data.error?.details||data.error?.code||"Restore failed");
      setNotice(`Revision ${revision.revision_number} restored as draft revision ${data.revision_number}`);location.reload();
    } catch (error) { setNotice(error instanceof Error?error.message:"Restore failed");setRestoring(null); }
  };
  const importBundle = async (file?: File) => {
    if (!file) return;
    setNotice("Validating lab backup without changing the workspace…");
    try {
      const response = await fetch(`/api/v1/labs/${labId}/import-preview/`, {method:"POST",credentials:"same-origin",
        headers:{"Content-Type":"application/vnd.containerlab.studio.lab+json","X-CSRFToken":csrf()},body:file});
      const data=await response.json();
      if (!response.ok) throw new Error(data.error?.details || "Backup validation failed");
      setBundleRestore({file,preview:data});setNotice("Backup validated — review the restore impact");
    } catch (error) { setNotice(error instanceof Error ? error.message : "Backup restore failed"); }
    finally { if (importInput.current) importInput.current.value=""; }
  };
  const confirmBundleRestore=async()=>{
    if (!bundleRestore)return;setBundleRestoring(true);setNotice("Restoring validated lab backup…");
    try {const response=await fetch(`/api/v1/labs/${labId}/import/`,{method:"POST",credentials:"same-origin",headers:{
      "Content-Type":"application/vnd.containerlab.studio.lab+json","X-CSRFToken":csrf(),"Idempotency-Key":crypto.randomUUID(),
      "X-Expected-Draft":bundleRestore.preview.expected_current_draft||"none","X-Edit-Lease":leaseToken.current},body:bundleRestore.file});const data=await response.json();
      if(!response.ok)throw new Error(data.error?.details||data.error?.code||"Restore failed");
      setNotice(`Restored ${data.node_count} devices and ${data.link_count} links from verified backup`);location.reload();
    }catch(error){setNotice(error instanceof Error?error.message:"Backup restore failed");setBundleRestoring(false)}
  };
  const deploy = async () => {
    if (dirty) { setNotice("Save the draft before deployment"); return; }
    if (!confirm("Publish this revision and deploy it to Kubernetes? The published revision becomes immutable.")) return;
    setDeploying(true); setNotice("Scheduling deployment…");
    try {
      const response = await fetch(`/api/v1/labs/${labId}/deploy/`, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf(), "Idempotency-Key": crypto.randomUUID(), "X-Edit-Lease":leaseToken.current }, body: "{}" });
      const data = await response.json();
      if (!response.ok) throw new Error(Array.isArray(data.error?.details) ? data.error.details.join(" · ") : data.error?.details || data.error?.code || "Deployment failed");
      setNotice("Deployment accepted — opening runtime view");
      location.href = "/deployments/";
    } catch (error) { setNotice(error instanceof Error ? error.message : "Deployment failed"); setDeploying(false); }
  };
  const undo = () => {
    const previous = history.at(-1);
    if (!previous) return;
    setFuture((f) => [
      { nodes: structuredClone(nodes), edges: structuredClone(edges),annotations:structuredClone(annotations) },
      ...f,
    ]);
    setNodes(previous.nodes);
    setEdges(previous.edges);
    setAnnotations(previous.annotations);
    setHistory((h) => h.slice(0, -1));
    setDirty(true);
  };
  const redo = () => {
    const next = future[0];
    if (!next) return;
    setHistory((h) => [
      ...h,
      { nodes: structuredClone(nodes), edges: structuredClone(edges),annotations:structuredClone(annotations) },
    ]);
    setNodes(next.nodes);
    setEdges(next.edges);
    setAnnotations(next.annotations);
    setFuture((f) => f.slice(1));
    setDirty(true);
  };
  const updateName = (value: string) => {
    if (!selectedNode) return;
    setNodes((ns) =>
      ns.map((n) =>
        n.id === selectedNode.id
          ? {
              ...n,
              data: {
                ...n.data,
                label: value
                  .toLowerCase()
                  .replace(/[^a-z0-9-]/g, "-")
                  .slice(0, 63),
              },
            }
          : n,
      ),
    );
    setDirty(true);
  };
  const removeSelected = () => {
    snapshot();
    const selectedIds=new Set(selectedDeviceNodes.length>1?selectedDeviceNodes.map(node=>node.id):selectedNode?[selectedNode.id]:[]);
    if (selectedIds.size) {
      setEdges((es) =>
        es.filter(
          (e) => !selectedIds.has(e.source) && !selectedIds.has(e.target),
        ),
      );
      setNodes((ns) => ns.filter((n) => !selectedIds.has(n.id)));
    } else if (selectedEdge)
      setEdges((es) => es.filter((e) => e.id !== selectedEdge.id));
    else if(selectedAnnotation)setAnnotations(items=>items.filter(item=>item.id!==selectedAnnotation.id));
    setSelected(null);
    setDirty(true);
  };
  const duplicateSelected = () => {
    const source=selectedDeviceNodes.length?selectedDeviceNodes:selectedNode?[selectedNode]:[];
    if (!source.length || !workspaceReady || !canEdit) return;
    snapshot();
    const result=duplicateSubgraph(nodes,edges,new Set(source.map(node=>node.id)),()=>crypto.randomUUID());
    setNodes(current=>[...current.map(node=>({...node,selected:false})),...result.nodes]);
    setEdges(current=>[...current.map(edge=>({...edge,selected:false})),...result.edges]);
    setSelected(result.nodes.length===1?result.nodes[0].id:null);setDirty(true);
    setNotice(`Duplicated ${result.nodes.length} device${result.nodes.length===1?"":"s"} and ${result.edges.length} internal link${result.edges.length===1?"":"s"}`);
  };
  const arrangeAll = () => {
    if(nodes.length<2||!workspaceReady||!canEdit)return;snapshot();setNodes(arrangeTopology(nodes,edges,annotations));setDirty(true);setSelected(null);
    setNotice(`Arranged ${nodes.length} devices into linked groups`);requestAnimationFrame(()=>rf?.fitView({padding:.2,duration:350}));
  };
  const alignSelection = (axis:"row"|"column") => {
    if(selectedDeviceNodes.length<2)return;snapshot();
    setNodes(current=>alignSelectedNodes(current,new Set(selectedDeviceNodes.map(node=>node.id)),axis));setDirty(true);
    setNotice(`Aligned ${selectedDeviceNodes.length} devices into a ${axis}`);
  };
  useEffect(()=>{
    const keyboard=(event:KeyboardEvent)=>{
      const target=event.target as HTMLElement|null;
      if(target?.matches("input, textarea, select, [contenteditable=true]"))return;
      if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==="d"&&(selectedDeviceNodes.length||selectedNode)){
        event.preventDefault();duplicateSelected();
      }else if(event.key==="Delete"&&(selectedDeviceNodes.length||selectedNode||selectedEdge||selectedAnnotation)){
        event.preventDefault();removeSelected();
      }
    };
    addEventListener("keydown",keyboard);return()=>removeEventListener("keydown",keyboard);
  },[nodes,edges,annotations,selected,selectedDeviceNodes.length,workspaceReady]);
  const errors = useMemo(() => {
    const issues: string[] = [];
    const names = new Set<string>();
    nodes.forEach((n) => {
      if (names.has(n.data.label))
        issues.push(`Duplicate node name: ${n.data.label}`);
      names.add(n.data.label);
      if (!n.data.verified)
        issues.push(`${n.data.label}: template is unverified`);
      if (!n.data.imageId) issues.push(`${n.data.label}: select a published image`);
      if (n.data.startupConfigRequired && !String(n.data.startupConfig || "").trim())
        issues.push(`${n.data.label}: startup configuration is required`);
      const linkedInterfaces = n.data.interfaces.filter((iface) => used.has(`${n.id}:${iface}`)).length;
      if (n.data.requiredInterfaces > linkedInterfaces)
        issues.push(`${n.data.label}: connect at least ${n.data.requiredInterfaces} interfaces`);
    });
    if (!nodes.length) issues.push("Add at least one device");
    return issues;
  }, [nodes, used]);
  return (
    <div className={`workspace-shell ${editLease&&!canEdit?"read-only":""}`}>
      <header className="workspace-top">
        <a href="/labs/" className="back">
          ‹
        </a>
        <div className="workspace-brand">
          <span className="mini-mark">◇</span>
          <p>
            <strong>{labName}</strong>
            <small>Topology workspace · Draft v{editVersion || 1}</small>
          </p>
        </div>
        <div className="toolbar">
          <button onClick={undo} disabled={!canEdit||!history.length} title="Undo">
            ↶
          </button>
          <button onClick={redo} disabled={!canEdit||!future.length} title="Redo">
            ↷
          </button>
          <i></i>
          <button onClick={() => rf?.fitView({ padding: 0.2 })}>Fit</button>
          <button onClick={arrangeAll} disabled={!canEdit||!workspaceReady||nodes.length<2} title="Automatically arrange linked device groups (undoable)">⌘ Arrange</button>
          <button onClick={duplicateSelected} disabled={!canEdit||!workspaceReady||(!selectedDeviceNodes.length&&!selectedNode)} title="Duplicate selected devices and their internal links (Ctrl/Cmd+D)">⧉ Duplicate{selectedDeviceNodes.length>1?` ${selectedDeviceNodes.length}`:""}</button>
          <button onClick={()=>addAnnotation("note")} disabled={!canEdit||!workspaceReady} title="Add a movable text note">＋ Note</button>
          <button onClick={()=>addAnnotation("region")} disabled={!canEdit||!workspaceReady} title="Add a colored topology region">▧ Region</button>
          <button onClick={exportBundle} disabled={dirty} title="Download a product-native lab backup. No YAML editing is required.">Backup</button>
          <button onClick={() => setCloneOpen(true)} disabled={dirty}>Save as</button>
          <button onClick={openHistory} disabled={dirty}>History</button>
          <button onClick={() => importInput.current?.click()} disabled={!canEdit} title="Restore a ContainerLab Studio backup. This does not require a YAML file.">Restore</button>
          <input ref={importInput} className="file-input" type="file" accept=".json,.clabstudio.json,application/json" onChange={(e)=>importBundle(e.target.files?.[0])}/>
          <button>
            Validate{" "}
            <span className={errors.length ? "warn" : "ok"}>
              {errors.length}
            </span>
          </button>
          <button className="deploy" onClick={deploy} disabled={!canEdit||errors.length > 0 || dirty || deploying}>
            {deploying ? "Deploying…" : "▶ Deploy"}
          </button>
          <button className="save" onClick={save} disabled={!canEdit||!workspaceReady || saving || !dirty}>
            {saving ? "Saving…" : dirty ? "Save draft" : "Saved ✓"}
          </button>
        </div>
      </header>
      {editLease&&!canEdit&&<div className="edit-lease-banner" role="status"><strong>Read-only workspace</strong><span>{editLease.owner||"Another operator"} is editing this topology. Your view is protected from overwriting their changes.</span><small>{editLease.expires_at?`Available after ${new Date(editLease.expires_at).toLocaleTimeString()}`:"Waiting for the editing session to be released"}</small></div>}
      {cloneOpen && <div className="modal-backdrop" role="presentation" onMouseDown={()=>!cloning&&setCloneOpen(false)}>
        <section className="clone-dialog" role="dialog" aria-modal="true" aria-labelledby="clone-title" onMouseDown={(event)=>event.stopPropagation()}>
          <p className="dialog-eyebrow">LAB WORKFLOW</p><h2 id="clone-title">Save topology as a new lab</h2>
          <p>Create an independent editable copy with the same devices, links, pinned images, annotations, and startup configurations.</p>
          <label>New lab name<input autoFocus maxLength={120} value={cloneName} onChange={(event)=>setCloneName(event.target.value)} onKeyDown={(event)=>event.key==="Enter"&&cloneLab()}/></label>
          <div><button onClick={()=>setCloneOpen(false)} disabled={cloning}>Cancel</button><button className="primary" onClick={cloneLab} disabled={cloning||!cloneName.trim()}>{cloning?"Creating…":"Create lab copy"}</button></div>
        </section>
      </div>}
      {bundleRestore&&<div className="modal-backdrop" role="presentation" onMouseDown={()=>!bundleRestoring&&setBundleRestore(null)}>
        <section className="bundle-dialog" role="dialog" aria-modal="true" aria-labelledby="bundle-title" onMouseDown={event=>event.stopPropagation()}>
          <header><div><p className="dialog-eyebrow">VERIFIED BACKUP</p><h2 id="bundle-title">Restore topology backup</h2><p>Review the server-validated contents and impact before replacing the editable draft.</p></div><button aria-label="Close backup preview" onClick={()=>setBundleRestore(null)} disabled={bundleRestoring}>×</button></header>
          <div className="bundle-source"><span>Source lab</span><strong>{bundleRestore.preview.source_lab}</strong><code>SHA-256 {bundleRestore.preview.checksum}</code></div>
          <div className="bundle-facts"><article><span>Devices</span><strong>{bundleRestore.preview.node_count}</strong></article><article><span>Links</span><strong>{bundleRestore.preview.link_count}</strong></article><article><span>Configurations</span><strong>{bundleRestore.preview.configured_node_count}</strong></article><article><span>Images</span><strong>{bundleRestore.preview.image_count}</strong></article></div>
          <div className={`bundle-impact ${bundleRestore.preview.deployable?"":"has-issues"}`}><strong>{bundleRestore.preview.deployable?"Restore impact":"Restore impact · deployment attention required"}</strong><ul><li>{bundleRestore.preview.will_replace_draft?"The current editable draft will be replaced.":"A new editable draft will be created."}</li>{dirty&&<li>Unsaved browser changes will be discarded.</li>}<li>{bundleRestore.preview.preserved_published_revisions} published revision(s) remain immutable.</li><li>{bundleRestore.preview.running_deployments_unchanged} active deployment revision(s) remain unchanged.</li>{bundleRestore.preview.deployability_issues.map(issue=><li key={issue} className="bundle-issue">{issue}</li>)}</ul><small>{bundleRestore.preview.templates.join(" · ")}</small></div>
          <footer><button onClick={()=>setBundleRestore(null)} disabled={bundleRestoring}>Cancel</button><button className="primary" onClick={confirmBundleRestore} disabled={bundleRestoring}>{bundleRestoring?"Restoring…":"Restore verified backup"}</button></footer>
        </section>
      </div>}
      {historyOpen && <div className="modal-backdrop" role="presentation" onMouseDown={()=>!restoring&&setHistoryOpen(false)}>
        <section className="history-dialog" role="dialog" aria-modal="true" aria-labelledby="history-title" onMouseDown={(event)=>event.stopPropagation()}>
          <header><div><p className="dialog-eyebrow">VERSION CONTROL</p><h2 id="history-title">{revisionComparison?`Revision ${revisionComparison.left.revision_number} → ${revisionComparison.right.revision_number}`:"Topology revision history"}</h2><p>{revisionComparison?"Structured topology changes; startup configuration content remains encrypted.":"Select two versions to compare. Restore creates a new editable draft."}</p></div><button aria-label="Close revision history" onClick={()=>setHistoryOpen(false)} disabled={!!restoring}>×</button></header>
          {revisionComparison?<div className="revision-comparison"><div className="comparison-facts"><article><span>Devices</span><strong>+{revisionComparison.summary.nodes_added} / −{revisionComparison.summary.nodes_removed} / ~{revisionComparison.summary.nodes_modified}</strong></article><article><span>Links</span><strong>+{revisionComparison.summary.links_added} / −{revisionComparison.summary.links_removed} / ~{revisionComparison.summary.links_modified}</strong></article><article><span>Canvas</span><strong>{revisionComparison.summary.canvas_changed?"Changed":"Unchanged"}</strong></article><article><span>Objects</span><strong>{revisionComparison.summary.annotations_changed} changed</strong></article></div><section><h3>Device changes</h3>{!revisionComparison.nodes.added.length&&!revisionComparison.nodes.removed.length&&!revisionComparison.nodes.modified.length?<p>No device changes.</p>:<ul>{revisionComparison.nodes.added.map(name=><li key={`a-${name}`}><b>Added</b> {name}</li>)}{revisionComparison.nodes.removed.map(name=><li key={`r-${name}`}><b>Removed</b> {name}</li>)}{revisionComparison.nodes.modified.map(node=><li key={`m-${node.name}`}><b>Modified</b> {node.name}<small>{node.fields.map(field=>field.field).join(" · ")}</small></li>)}</ul>}</section><section><h3>Link changes</h3>{!revisionComparison.links.added.length&&!revisionComparison.links.removed.length&&!revisionComparison.links.modified.length?<p>No link changes.</p>:<ul>{revisionComparison.links.added.map(link=><li key={`a-${link}`}><b>Added</b> {link}</li>)}{revisionComparison.links.removed.map(link=><li key={`r-${link}`}><b>Removed</b> {link}</li>)}{revisionComparison.links.modified.map(link=><li key={`m-${link.link}`}><b>Modified</b> {link.link}<small>{link.fields.join(" · ")}</small></li>)}</ul>}</section></div>:<div className="revision-list">{historyLoading?<p className="history-empty">Loading revision history…</p>:revisions.length===0?<p className="history-empty">No saved revisions yet.</p>:revisions.map((revision)=><article key={revision.id} className={revision.is_current_draft?"current":""}>
            <input type="checkbox" aria-label={`Select revision ${revision.revision_number} for comparison`} checked={selectedRevisionIds.includes(revision.id)} onChange={()=>setSelectedRevisionIds(current=>current.includes(revision.id)?current.filter(id=>id!==revision.id):[...current.slice(-1),revision.id])}/><span className="revision-mark">{revision.revision_number}</span><div className="revision-copy"><strong>Revision {revision.revision_number} {revision.is_current_draft&&<em>Current draft</em>}</strong><small>{new Date(revision.created_at).toLocaleString()} · {revision.node_count} devices · {revision.link_count} links</small><code>{revision.topology_checksum.slice(0,12)}</code></div>
            <span className={`revision-state ${revision.immutable?"published":"draft"}`}>{revision.immutable?`Published · ${revision.deployment_count} deploy${revision.deployment_count===1?"":"s"}`:"Editable"}</span>
            <button onClick={()=>restoreRevision(revision)} disabled={revision.is_current_draft||!!restoring}>{restoring===revision.id?"Restoring…":"Restore"}</button>
          </article>)}</div>}
          <footer><span>{revisionComparison?"Comparison is read-only and does not change the draft or runtime.":`${selectedRevisionIds.length} of 2 revisions selected`}</span><div>{revisionComparison?<button onClick={()=>setRevisionComparison(null)}>Back</button>:<button onClick={compareSelectedRevisions} disabled={selectedRevisionIds.length!==2||comparingRevisions}>{comparingRevisions?"Comparing…":"Compare"}</button>}<button onClick={()=>setHistoryOpen(false)} disabled={!!restoring}>Close</button></div></footer>
        </section>
      </div>}
      <div className="workspace-body">
        <aside className="palette">
          <div className="pane-title">
            <p>
              <strong>Devices</strong>
              <small>Drag onto canvas</small>
            </p>
            <button onClick={()=>setTemplateQuery("")} title={templateQuery?"Clear device filter":"Search device templates"}>{templateQuery?"×":"⌕"}</button>
          </div>
          <input className="palette-search" placeholder="Filter devices…" value={templateQuery} onChange={event=>setTemplateQuery(event.target.value)} />
          {[...new Set(filteredTemplates.map((t) => t.category))].map((category) => (
            <section key={category}>
              <h3>{category}</h3>
              {filteredTemplates
                .filter((t) => t.category === category)
                .map((t) => (
                  <div
                    className="palette-item"
                    key={t.id}
                    draggable
                    onDragStart={(e) => {
                      e.dataTransfer.setData("template", t.id);
                      e.dataTransfer.effectAllowed = "copy";
                    }}
                  >
                    <span className={`palette-glyph ${t.icon}`}>
                      {iconFor(t.icon)}
                    </span>
                    <p>
                      <strong>{t.name}</strong>
                      <small>
                        {t.interfaces.length} interfaces ·{" "}
                        {t.verified ? "Verified" : "Unverified"}
                      </small>
                    </p>
                    {t.privileged && <em>!</em>}
                    <b>⋮⋮</b>
                  </div>
                ))}
            </section>
          ))}
          {!filteredTemplates.length&&<div className="palette-empty"><span>⌕</span><strong>No matching devices</strong><small>Search by name, kind, or category.</small></div>}
        </aside>
        <main
          className="canvas"
          onDrop={drop}
          onDragOver={(e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = "copy";
          }}
        >
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onInit={(instance) => setRf(instance)}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            isValidConnection={validConnection}
            onNodeClick={(_, n) => setSelected(n.id)}
            onEdgeClick={(_, e) => setSelected(e.id)}
            onPaneClick={() => setSelected(null)}
            onSelectionChange={({nodes:selectedNodes,edges:selectedEdges})=>{
              if(selectedNodes.length===1&&!selectedEdges.length)setSelected(selectedNodes[0].id);
              else if(selectedEdges.length===1&&!selectedNodes.length)setSelected(selectedEdges[0].id);
              else if(selectedNodes.length>1)setSelected(null);
            }}
            selectionOnDrag
            nodesDraggable={canEdit}
            nodesConnectable={canEdit}
            panOnDrag={[1,2]}
            deleteKeyCode={null}
            minZoom={0.25}
            maxZoom={2}
          >
            <Background color="#29435a" gap={24} size={1} />
            <ViewportPortal>
              {annotations.map(annotation=><div key={annotation.id}
                className={`topology-annotation ${annotation.type} color-${annotation.color} ${selected===`annotation:${annotation.id}`?"selected":""}`}
                style={{transform:`translate(${annotation.x}px,${annotation.y}px)`,width:annotation.width,height:annotation.height,
                  zIndex:annotation.zIndex,fontSize:annotation.fontSize}}
                onPointerDown={event=>beginAnnotationGesture(event,annotation)} onClick={event=>{event.stopPropagation();setSelected(`annotation:${annotation.id}`)}}>
                <span>{annotation.text}</span><button type="button" className="annotation-resize" aria-label={`Resize ${annotation.type}`}
                  onPointerDown={event=>beginAnnotationGesture(event,annotation,true)}>⌟</button>
              </div>)}
            </ViewportPortal>
            <Controls showInteractive={false} />
            <MiniMap
              pannable
              zoomable
              nodeColor="#1a7791"
              maskColor="rgba(5,13,23,.72)"
            />
            <Panel position="top-left" className="canvas-hint">
              {!workspaceReady?"Loading saved topology…":nodes.length
                ? `${nodes.length} devices · ${edges.length} links · ${annotations.length} canvas objects`
                : annotations.length?`${annotations.length} canvas objects · Drag a device here to continue`:"Drag a device here to begin"}
            </Panel>
          </ReactFlow>
        </main>
        <aside className="inspector">
          <div className="pane-title">
            <p>
              <strong>Inspector</strong>
              <small>
                {selectedNode
                  ? "Device selected"
                  : selectedEdge
                    ? "Link selected"
                    : selectedAnnotation?`${selectedAnnotation.type} selected`:"Nothing selected"}
              </small>
            </p>
          </div>
          {selectedDeviceNodes.length>1 ? (
            <div className="properties bulk-properties">
              <div className="bulk-selection-summary"><strong>{selectedDeviceNodes.length} devices selected</strong><span>{edges.filter(edge=>selectedDeviceNodes.some(node=>node.id===edge.source)&&selectedDeviceNodes.some(node=>node.id===edge.target)).length} internal links</span></div>
              <p>Drag the selection together, duplicate the complete subgraph, or remove it. Pinned templates, images, startup configurations, and internal interface links are preserved in each copy.</p>
              <div className="bulk-align"><button onClick={()=>alignSelection("row")}>Align row</button><button onClick={()=>alignSelection("column")}>Align column</button></div>
              <button className="bulk-primary" onClick={duplicateSelected}>⧉ Duplicate selected subgraph</button>
              <button className="danger" onClick={removeSelected}>Remove selected devices</button>
              <small className="keyboard-help">Shift-click to adjust selection · Ctrl/Cmd+D to duplicate · Delete to remove</small>
            </div>
          ) : selectedNode ? (
            <div className="properties">
              <label>
                Node name
                <input
                  value={selectedNode.data.label}
                  onChange={(e) => updateName(e.target.value)}
                />
              </label>
              <label>
                Device kind
                <input value={selectedNode.data.kind} disabled />
              </label>
              <label>
                Published image
                <select value={String(selectedNode.data.imageId)} onChange={(e) => updateImage(e.target.value)}>
                  <option value="">Select a digest-pinned image…</option>
                  {images.map((image) => <option key={image.id} value={image.id}>{image.name} · {image.architecture} · {image.status}</option>)}
                </select>
              </label>
              <label>
                Startup configuration {selectedNode.data.startupConfigRequired ? "· Required" : "· Optional"}
                <textarea value={String(selectedNode.data.startupConfig || "")} placeholder="Optional device startup configuration"
                  disabled={!selectedNode.data.startupConfigSupported}
                  onChange={(e)=>{setNodes((ns)=>ns.map((n)=>n.id===selectedNode.id?{...n,data:{...n.data,startupConfig:e.target.value}}:n));setDirty(true);}} />
                <small className="field-help">
                  {selectedNode.data.startupConfigSupported
                    ? `${selectedNode.data.configurationLanguage} · Encrypted at rest · Maximum 1 MiB`
                    : "This template does not support startup configuration"}
                </small>
              </label>
              <label>
                Saved startup order
                <input type="number" min="1" max="250" value={selectedNode.data.startupOrder??""} placeholder="Not included"
                  onChange={(e)=>{const value=e.target.value===""?null:Math.max(1,Math.min(250,Number(e.target.value)));setNodes(ns=>ns.map(n=>n.id===selectedNode.id?{...n,data:{...n.data,startupOrder:value}}:n));setDirty(true)}} />
                <small className="field-help">Devices with an order are offered as a saved staged-start plan in the runtime page. Equal values are ordered by name.</small>
              </label>
              <div className="property-block">
                <span>Runtime status</span>
                <em className="status-pill">● Stopped</em>
              </div>
              <div className="property-block column">
                <span>Interfaces</span>
                <div className="interface-grid">
                  {selectedNode.data.interfaces.map((i) => (
                    <b
                      className={
                        used.has(`${selectedNode.id}:${i}`) ? "in-use" : ""
                      }
                      key={i}
                    >
                      {i}
                      <small>
                        {used.has(`${selectedNode.id}:${i}`)
                          ? "linked"
                          : "free"}
                      </small>
                    </b>
                  ))}
                </div>
              </div>
              <button className="danger" onClick={removeSelected}>
                Remove device
              </button>
            </div>
          ) : selectedEdge ? (
            <div className="properties">
              <label>
                Link label
                <input
                  value={String(selectedEdge.label || "")}
                  onChange={(e) => {
                    setEdges((es) =>
                      es.map((x) =>
                        x.id === selectedEdge.id
                          ? { ...x, label: e.target.value }
                          : x,
                      ),
                    );
                    setDirty(true);
                  }}
                />
              </label>
              <div className="link-detail">
                <span>
                  {nodes.find((n) => n.id === selectedEdge.source)?.data.label}
                </span>
                <b>
                  {ifaceFromHandle(selectedEdge.sourceHandle)} ↔{" "}
                  {ifaceFromHandle(selectedEdge.targetHandle)}
                </b>
                <span>
                  {nodes.find((n) => n.id === selectedEdge.target)?.data.label}
                </span>
              </div>
              <label>
                Link state
                <select>
                  <option>Enabled</option>
                  <option disabled>Disabled (runtime unsupported)</option>
                </select>
              </label>
              <button className="danger" onClick={removeSelected}>
                Remove link
              </button>
            </div>
          ) : selectedAnnotation ? (
            <div className="properties annotation-properties">
              <label>Object type<input value={selectedAnnotation.type==="note"?"Text note":"Colored region"} disabled /></label>
              <label>Text<textarea maxLength={2000} value={selectedAnnotation.text}
                onChange={event=>updateAnnotation(selectedAnnotation.id,{text:event.target.value})}/><small className="field-help">Visible on the topology canvas and included in native backup/restore.</small></label>
              <label>Color<select value={selectedAnnotation.color} onChange={event=>updateAnnotation(selectedAnnotation.id,{color:event.target.value as TopologyAnnotation["color"]})}>
                <option value="cyan">Cyan</option><option value="blue">Blue</option><option value="violet">Violet</option><option value="amber">Amber</option><option value="rose">Rose</option><option value="green">Green</option><option value="slate">Slate</option>
              </select></label>
              <label>Font size<input type="number" min={10} max={32} value={selectedAnnotation.fontSize} onChange={event=>updateAnnotation(selectedAnnotation.id,{fontSize:Number(event.target.value)})}/></label>
              <div className="annotation-dimensions"><label>Width<input type="number" min={80} max={2000} value={Math.round(selectedAnnotation.width)} onChange={event=>updateAnnotation(selectedAnnotation.id,{width:Number(event.target.value)})}/></label><label>Height<input type="number" min={40} max={1600} value={Math.round(selectedAnnotation.height)} onChange={event=>updateAnnotation(selectedAnnotation.id,{height:Number(event.target.value)})}/></label></div>
              <label>Layer<select value={selectedAnnotation.zIndex} onChange={event=>updateAnnotation(selectedAnnotation.id,{zIndex:Number(event.target.value)})}><option value={-20}>Behind all devices</option><option value={-10}>Behind devices</option><option value={10}>Above devices</option><option value={20}>Top annotation</option></select></label>
              <button className="danger" onClick={removeSelected}>Remove canvas object</button>
            </div>
          ) : (
            <div className="inspector-empty">
              <span>⌁</span>
              <h3>Select an object</h3>
              <p>
                Choose a device or link to edit its properties and inspect
                interfaces.
              </p>
            </div>
          )}
          <div className="validation">
            <div>
              <strong>Validation</strong>
              <span className={errors.length ? "warn" : "ok"}>
                {errors.length ? `${errors.length} issues` : "Ready"}
              </span>
            </div>
            {errors.slice(0, 4).map((x) => (
              <p key={x}>! {x}</p>
            ))}
            {!errors.length && (
              <p className="valid">✓ Topology is structurally valid</p>
            )}
          </div>
        </aside>
      </div>
      <footer className="workspace-footer">
        <span>
          <i className={dirty ? "dirty" : "clean"}></i>
          {notice}
        </span>
        <span>
          Interfaces are point-to-point · Used interfaces cannot be linked twice
        </span>
        <span>Canvas autosafety: on</span>
      </footer>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <ReactFlowProvider>
    <Workspace />
  </ReactFlowProvider>,
);
