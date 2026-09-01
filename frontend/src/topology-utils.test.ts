import { describe, expect, it } from "vitest";

import { duplicateSubgraph, interfaceFromHandle, interfaceIsAvailable } from "./topology-utils";

describe("topology interface helpers", () => {
  it("maps React Flow endpoint handles to device interface names", () => {
    expect(interfaceFromHandle("router-1:eth1")).toBe("eth1");
    expect(interfaceFromHandle("router-1:ge-0/0/1")).toBe("ge-0/0/1");
    expect(interfaceFromHandle(undefined)).toBe("");
  });

  it("rejects empty and already-used point-to-point handles", () => {
    const used = new Set(["router-1:eth1"]);
    expect(interfaceIsAvailable("router-1:eth1", used)).toBe(false);
    expect(interfaceIsAvailable("router-1:eth2", used)).toBe(true);
    expect(interfaceIsAvailable("", used)).toBe(false);
  });

  it("duplicates a selected subgraph with unique identities, names, configuration, and internal links", () => {
    let sequence = 0;
    const nodes = [
      { id: "r1", position: { x: 10, y: 20 }, data: { label: "router", startupConfig: "hostname r1" } },
      { id: "r2", position: { x: 210, y: 20 }, data: { label: "router-copy", startupConfig: "hostname r2" } },
      { id: "outside", position: { x: 410, y: 20 }, data: { label: "outside", startupConfig: "" } },
    ];
    const edges = [
      { id: "internal", source: "r1", target: "r2", sourceHandle: "s:eth1", targetHandle: "t:eth1" },
      { id: "external", source: "r2", target: "outside", sourceHandle: "s:eth2", targetHandle: "t:eth1" },
    ];
    const result = duplicateSubgraph(nodes, edges, new Set(["r1", "r2"]), () => `new-${++sequence}`);
    expect(result.nodes.map((node) => [node.id, node.data.label, node.position])).toEqual([
      ["new-1", "router-copy-2", { x: 70, y: 80 }], ["new-2", "router-copy-copy", { x: 270, y: 80 }],
    ]);
    expect(result.nodes[0].data.startupConfig).toBe("hostname r1");
    expect(result.edges).toHaveLength(1);
    expect(result.edges[0]).toMatchObject({ id: "new-3", source: "new-1", target: "new-2", sourceHandle: "s:eth1", targetHandle: "t:eth1" });
  });
});
