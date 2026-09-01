import { describe, expect, it } from "vitest";

import { alignSelectedNodes, arrangeTopology, duplicateSubgraph, interfaceFromHandle, interfaceIsAvailable } from "./topology-utils";
import { requestedConsoleDevice, visibleConsoleIds } from "./console-utils";

describe("topology interface helpers", () => {
  it("accepts only same-origin live-map console requests", () => {
    const message={type:"open-device-console",deviceId:"device-1"};
    expect(requestedConsoleDevice("https://studio.example","https://studio.example",message)).toBe("device-1");
    expect(requestedConsoleDevice("https://attacker.example","https://studio.example",message)).toBe("");
    expect(requestedConsoleDevice("https://studio.example","https://studio.example",{...message,deviceId:42})).toBe("");
  });
  it("selects one active console or a stable two-pane split",()=>{
    expect(visibleConsoleIds(["r1","r2"],"r2",false)).toEqual(["r2"]);
    expect(visibleConsoleIds(["r1","r2"],"r2",true)).toEqual(["r1","r2"]);
    expect(visibleConsoleIds(["r1"],"r1",true)).toEqual(["r1"]);
  });
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

  it("arranges linked components deterministically and separates disconnected groups", () => {
    const nodes=["d","b","a","c"].map((id)=>({id,position:{x:999,y:999},data:{label:id}}));
    const edges=[{source:"a",target:"b"},{source:"c",target:"d"}];
    const first=arrangeTopology(nodes,edges);const second=arrangeTopology([...nodes].reverse(),edges);
    expect(Object.fromEntries(first.map((node)=>[node.id,node.position]))).toEqual(Object.fromEntries(second.map((node)=>[node.id,node.position])));
    const positions=Object.fromEntries(first.map((node)=>[node.id,node.position]));
    expect(positions.a.x).not.toBe(positions.b.x);
    expect(Math.min(positions.c.y,positions.d.y)).toBeGreaterThan(Math.max(positions.a.y,positions.b.y));
  });

  it("aligns only selected devices into a row or column", () => {
    const nodes=[{id:"a",position:{x:0,y:10}},{id:"b",position:{x:100,y:30}},{id:"c",position:{x:200,y:90}}];
    const row=alignSelectedNodes(nodes,new Set(["a","b"]),"row");
    expect(row.map((node)=>node.position)).toEqual([{x:0,y:20},{x:100,y:20},{x:200,y:90}]);
    const column=alignSelectedNodes(nodes,new Set(["a","b"]),"column");
    expect(column.map((node)=>node.position)).toEqual([{x:50,y:10},{x:50,y:30},{x:200,y:90}]);
  });

  it("keeps arranged devices clear of notes and regions", () => {
    const nodes=[
      {id:"a",position:{x:0,y:0},data:{label:"a",interfaces:["eth0","eth1"]}},
      {id:"b",position:{x:0,y:0},data:{label:"b",interfaces:["eth0","eth1"]}},
    ];
    const obstacle={x:80,y:60,width:500,height:230};
    const arranged=arrangeTopology(nodes,[{source:"a",target:"b"}],[obstacle]);
    arranged.forEach((node)=>{
      const height=Math.max(120,60+node.data.interfaces.length*15);
      const overlaps=node.position.x<obstacle.x+obstacle.width+30&&node.position.x+160>obstacle.x-30&&
        node.position.y<obstacle.y+obstacle.height+30&&node.position.y+height>obstacle.y-30;
      expect(overlaps).toBe(false);
    });
  });
});
