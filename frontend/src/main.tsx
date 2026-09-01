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
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./style.css";
import "./clone.css";
import "./configuration.css";

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
type Snapshot = { nodes: Node<DeviceData>[]; edges: Edge[] };
type PublishedImage = { id: string; name: string; digest: string; architecture: string; status: string };
type RevisionSummary = { id:string; revision_number:number; edit_version:number; immutable:boolean; topology_checksum:string;
  node_count:number; link_count:number; deployment_count:number; created_at:string; is_current_draft:boolean };
type BundlePreview = { checksum:string; source_lab:string; destination_lab:string; node_count:number; link_count:number;
  configured_node_count:number; template_count:number; image_count:number; templates:string[]; will_replace_draft:boolean;
  preserved_published_revisions:number; running_deployments_unchanged:number; expected_current_draft:string|null;
  deployable:boolean; deployability_issues:string[] };
const params = new URLSearchParams(location.search);
const labId = params.get("lab") || "";
const labName = params.get("name") || "Topology Workspace";
const csrf = () =>
  document.cookie
    .split("; ")
    .find((x) => x.startsWith("csrftoken="))
    ?.split("=")[1] || "";
const ifaceFromHandle = (value: string | null | undefined) =>
  value?.split(":").slice(1).join(":") || "";
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
  const [currentDraftId, setCurrentDraftId] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [notice, setNotice] = useState("Ready");
  const [history, setHistory] = useState<Snapshot[]>([]);
  const [future, setFuture] = useState<Snapshot[]>([]);
  const [rf, setRf] = useState<any>(null);
  const counter = useRef(1);
  const importInput = useRef<HTMLInputElement>(null);
  const snapshot = useCallback(() => {
    setHistory((h) => [
      ...h.slice(-29),
      { nodes: structuredClone(nodes), edges: structuredClone(edges) },
    ]);
    setFuture([]);
  }, [nodes, edges]);
  useEffect(() => {
    Promise.all([
      fetch("/api/v1/topology/templates/").then((r) => r.json()),
      fetch(`/api/v1/labs/${labId}/topology/images/`).then((r) => r.json()),
      fetch(`/api/v1/labs/${labId}/topology/`).then((r) => r.json()),
    ])
      .then(([catalog, imageCatalog, doc]) => {
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
        setEditVersion(doc.editVersion);
        counter.current = doc.nodes.length + 1;
        setNotice("Draft loaded");
      })
      .catch(() => setNotice("Unable to load workspace"));
  }, []);
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
  const onNodesChange = useCallback(
    (changes: NodeChange<Node<DeviceData>>[]) => {
      setNodes((n) => applyNodeChanges(changes, n));
      if (
        changes.some((change) =>
          ["add", "remove", "replace", "position"].includes(change.type),
        )
      )
        setDirty(true);
    },
    [],
  );
  const onEdgesChange = useCallback(
    (changes: EdgeChange<Edge>[]) => {
      if (changes.some((c) => c.type === "remove")) snapshot();
      setEdges((e) => applyEdgeChanges(changes, e));
      if (
        changes.some((change) =>
          ["add", "remove", "replace"].includes(change.type),
        )
      )
        setDirty(true);
    },
    [snapshot],
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
    [snapshot, validConnection],
  );
  const drop = useCallback(
    (event: DragEvent) => {
      event.preventDefault();
      const id = event.dataTransfer.getData("template");
      const template = templates.find((t) => t.id === id);
      if (!template || !rf) return;
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
        },
      };
      setNodes((n) => [...n, node]);
      setSelected(node.id);
      setDirty(true);
      setNotice(`${template.name} added`);
    },
    [templates, rf, nodes, snapshot],
  );
  const save = async () => {
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
        properties: { kind: n.data.kind },
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
      annotations: [],
    };
    try {
      const r = await fetch(`/api/v1/labs/${labId}/topology/`, {
        method: "PUT",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
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
    if (!selectedNode) return;
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
    setHistoryOpen(true);setHistoryLoading(true);
    try {
      const response=await fetch(`/api/v1/labs/${labId}/revisions/`,{credentials:"same-origin"});const data=await response.json();
      if (!response.ok) throw new Error(data.error?.details||"Unable to load revision history");
      setRevisions(data.revisions);setCurrentDraftId(data.current_draft);
    } catch (error) { setNotice(error instanceof Error?error.message:"Unable to load revision history");setHistoryOpen(false); }
    finally { setHistoryLoading(false); }
  };
  const restoreRevision = async (revision:RevisionSummary) => {
    if (!confirm(`Restore revision ${revision.revision_number} as a new editable draft? Your current saved draft will be replaced.`)) return;
    setRestoring(revision.id);setNotice(`Restoring revision ${revision.revision_number}…`);
    try {
      const response=await fetch(`/api/v1/labs/${labId}/revisions/${revision.id}/restore/`,{method:"POST",credentials:"same-origin",
        headers:{"Content-Type":"application/json","X-CSRFToken":csrf(),"Idempotency-Key":crypto.randomUUID()},
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
      "X-Expected-Draft":bundleRestore.preview.expected_current_draft||"none"},body:bundleRestore.file});const data=await response.json();
      if(!response.ok)throw new Error(data.error?.details||data.error?.code||"Restore failed");
      setNotice(`Restored ${data.node_count} devices and ${data.link_count} links from verified backup`);location.reload();
    }catch(error){setNotice(error instanceof Error?error.message:"Backup restore failed");setBundleRestoring(false)}
  };
  const deploy = async () => {
    if (dirty) { setNotice("Save the draft before deployment"); return; }
    if (!confirm("Publish this revision and deploy it to Kubernetes? The published revision becomes immutable.")) return;
    setDeploying(true); setNotice("Scheduling deployment…");
    try {
      const response = await fetch(`/api/v1/labs/${labId}/deploy/`, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf(), "Idempotency-Key": crypto.randomUUID() }, body: "{}" });
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
      { nodes: structuredClone(nodes), edges: structuredClone(edges) },
      ...f,
    ]);
    setNodes(previous.nodes);
    setEdges(previous.edges);
    setHistory((h) => h.slice(0, -1));
    setDirty(true);
  };
  const redo = () => {
    const next = future[0];
    if (!next) return;
    setHistory((h) => [
      ...h,
      { nodes: structuredClone(nodes), edges: structuredClone(edges) },
    ]);
    setNodes(next.nodes);
    setEdges(next.edges);
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
    if (selectedNode) {
      setEdges((es) =>
        es.filter(
          (e) => e.source !== selectedNode.id && e.target !== selectedNode.id,
        ),
      );
      setNodes((ns) => ns.filter((n) => n.id !== selectedNode.id));
    } else if (selectedEdge)
      setEdges((es) => es.filter((e) => e.id !== selectedEdge.id));
    setSelected(null);
    setDirty(true);
  };
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
    <div className="workspace-shell">
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
          <button onClick={undo} disabled={!history.length} title="Undo">
            ↶
          </button>
          <button onClick={redo} disabled={!future.length} title="Redo">
            ↷
          </button>
          <i></i>
          <button onClick={() => rf?.fitView({ padding: 0.2 })}>Fit</button>
          <button onClick={exportBundle} disabled={dirty} title="Download a product-native lab backup. No YAML editing is required.">Backup</button>
          <button onClick={() => setCloneOpen(true)} disabled={dirty}>Save as</button>
          <button onClick={openHistory} disabled={dirty}>History</button>
          <button onClick={() => importInput.current?.click()} title="Restore a ContainerLab Studio backup. This does not require a YAML file.">Restore</button>
          <input ref={importInput} className="file-input" type="file" accept=".json,.clabstudio.json,application/json" onChange={(e)=>importBundle(e.target.files?.[0])}/>
          <button>
            Validate{" "}
            <span className={errors.length ? "warn" : "ok"}>
              {errors.length}
            </span>
          </button>
          <button className="deploy" onClick={deploy} disabled={errors.length > 0 || dirty || deploying}>
            {deploying ? "Deploying…" : "▶ Deploy"}
          </button>
          <button className="save" onClick={save} disabled={saving || !dirty}>
            {saving ? "Saving…" : dirty ? "Save draft" : "Saved ✓"}
          </button>
        </div>
      </header>
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
          <header><div><p className="dialog-eyebrow">VERSION CONTROL</p><h2 id="history-title">Topology revision history</h2><p>Published versions remain immutable. Restore creates a new editable draft.</p></div><button aria-label="Close revision history" onClick={()=>setHistoryOpen(false)} disabled={!!restoring}>×</button></header>
          <div className="revision-list">{historyLoading?<p className="history-empty">Loading revision history…</p>:revisions.length===0?<p className="history-empty">No saved revisions yet.</p>:revisions.map((revision)=><article key={revision.id} className={revision.is_current_draft?"current":""}>
            <span className="revision-mark">{revision.revision_number}</span><div className="revision-copy"><strong>Revision {revision.revision_number} {revision.is_current_draft&&<em>Current draft</em>}</strong><small>{new Date(revision.created_at).toLocaleString()} · {revision.node_count} devices · {revision.link_count} links</small><code>{revision.topology_checksum.slice(0,12)}</code></div>
            <span className={`revision-state ${revision.immutable?"published":"draft"}`}>{revision.immutable?`Published · ${revision.deployment_count} deploy${revision.deployment_count===1?"":"s"}`:"Editable"}</span>
            <button onClick={()=>restoreRevision(revision)} disabled={revision.is_current_draft||!!restoring}>{restoring===revision.id?"Restoring…":"Restore"}</button>
          </article>)}</div>
          <footer><span>Restoring never changes an existing deployment.</span><button onClick={()=>setHistoryOpen(false)} disabled={!!restoring}>Close</button></footer>
        </section>
      </div>}
      <div className="workspace-body">
        <aside className="palette">
          <div className="pane-title">
            <p>
              <strong>Devices</strong>
              <small>Drag onto canvas</small>
            </p>
            <button>⌕</button>
          </div>
          <input className="palette-search" placeholder="Filter devices…" />
          {[...new Set(templates.map((t) => t.category))].map((category) => (
            <section key={category}>
              <h3>{category}</h3>
              {templates
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
            deleteKeyCode={null}
            minZoom={0.25}
            maxZoom={2}
          >
            <Background color="#29435a" gap={24} size={1} />
            <Controls showInteractive={false} />
            <MiniMap
              pannable
              zoomable
              nodeColor="#1a7791"
              maskColor="rgba(5,13,23,.72)"
            />
            <Panel position="top-left" className="canvas-hint">
              {nodes.length
                ? `${nodes.length} devices · ${edges.length} links`
                : "Drag a device here to begin"}
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
                    : "Nothing selected"}
              </small>
            </p>
          </div>
          {selectedNode ? (
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
