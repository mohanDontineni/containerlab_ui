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
      if (changes.some((c) => c.type !== "select")) setDirty(true);
    },
    [],
  );
  const onEdgesChange = useCallback(
    (changes: EdgeChange<Edge>[]) => {
      if (changes.some((c) => c.type === "remove")) snapshot();
      setEdges((e) => applyEdgeChanges(changes, e));
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
    if (dirty) { setNotice("Save the draft before exporting"); return; }
    location.href = `/api/v1/labs/${labId}/export/`;
  };
  const importBundle = async (file?: File) => {
    if (!file) return;
    if (dirty && !confirm("Importing replaces this unsaved draft. Continue?")) return;
    setNotice("Validating and importing lab bundle…");
    try {
      const response = await fetch(`/api/v1/labs/${labId}/import/`, {method:"POST",credentials:"same-origin",
        headers:{"Content-Type":"application/vnd.containerlab.studio.lab+json","X-CSRFToken":csrf()},body:file});
      const data=await response.json();
      if (!response.ok) throw new Error(data.error?.details || "Import failed");
      setNotice(`Imported ${data.node_count} devices and ${data.link_count} links`);
      location.reload();
    } catch (error) { setNotice(error instanceof Error ? error.message : "Import failed"); }
    finally { if (importInput.current) importInput.current.value=""; }
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
          <button onClick={exportBundle} disabled={dirty}>Export</button>
          <button onClick={() => importInput.current?.click()}>Import</button>
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
