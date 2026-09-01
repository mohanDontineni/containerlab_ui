export function interfaceFromHandle(value: string | null | undefined): string {
  return value?.split(":").slice(1).join(":") || "";
}

export function interfaceIsAvailable(
  handle: string,
  usedHandles: ReadonlySet<string>,
): boolean {
  return interfaceFromHandle(handle) !== "" && !usedHandles.has(handle);
}

export function duplicateSubgraph<
  N extends { id: string; position: { x: number; y: number }; data: { label: string }; selected?: boolean },
  E extends { id: string; source: string; target: string; selected?: boolean },
>(nodes: N[], edges: E[], selectedIds: ReadonlySet<string>, makeId: () => string, offset = 60) {
  const existingNames = new Set(nodes.map((node) => node.data.label));
  const idMap = new Map<string, string>();
  const copies = nodes.filter((node) => selectedIds.has(node.id)).map((node) => {
    const base = `${node.data.label}-copy`.slice(0, 63);
    let label = base;
    let suffix = 2;
    while (existingNames.has(label)) {
      const ending = `-${suffix++}`;
      label = `${base.slice(0, 63 - ending.length)}${ending}`;
    }
    existingNames.add(label);
    const id = makeId();
    idMap.set(node.id, id);
    return { ...structuredClone(node), id, position: { x: node.position.x + offset, y: node.position.y + offset },
      data: { ...structuredClone(node.data), label }, selected: true } as N;
  });
  const copiedEdges = edges.filter((edge) => idMap.has(edge.source) && idMap.has(edge.target)).map((edge) => ({
    ...structuredClone(edge), id: makeId(), source: idMap.get(edge.source)!, target: idMap.get(edge.target)!, selected: false,
  } as E));
  return { nodes: copies, edges: copiedEdges };
}

export function arrangeTopology<
  N extends { id: string; position: { x: number; y: number }; data: { label: string; interfaces?: unknown[] } },
  E extends { source: string; target: string },
>(nodes: N[], edges: E[], obstacles: ReadonlyArray<{x:number;y:number;width:number;height:number}> = [], horizontalGap = 270, verticalGap = 180): N[] {
  if (nodes.length < 2) return nodes.map((node) => structuredClone(node));
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const adjacency = new Map(nodes.map((node) => [node.id, new Set<string>()]));
  edges.forEach((edge) => {
    if (!byId.has(edge.source) || !byId.has(edge.target)) return;
    adjacency.get(edge.source)!.add(edge.target); adjacency.get(edge.target)!.add(edge.source);
  });
  const ordered = [...nodes].sort((a,b) => a.data.label.localeCompare(b.data.label) || a.id.localeCompare(b.id));
  const remaining = new Set(ordered.map((node) => node.id));
  const positions = new Map<string,{x:number;y:number}>();
  let componentY = 80;
  while (remaining.size) {
    const members: string[] = [];
    const seed = ordered.find((node) => remaining.has(node.id))!.id;
    const discover = [seed]; remaining.delete(seed);
    while (discover.length) {
      const id = discover.shift()!; members.push(id);
      [...adjacency.get(id)!].sort().forEach((neighbor) => { if (remaining.delete(neighbor)) discover.push(neighbor); });
    }
    const root = [...members].sort((a,b) => adjacency.get(b)!.size-adjacency.get(a)!.size || byId.get(a)!.data.label.localeCompare(byId.get(b)!.data.label))[0];
    const levels = new Map<string,number>([[root,0]]); const queue=[root];
    while(queue.length) {
      const id=queue.shift()!;
      [...adjacency.get(id)!].sort((a,b)=>byId.get(a)!.data.label.localeCompare(byId.get(b)!.data.label)).forEach((neighbor)=>{
        if(members.includes(neighbor)&&!levels.has(neighbor)){levels.set(neighbor,levels.get(id)!+1);queue.push(neighbor)}
      });
    }
    const grouped = new Map<number,string[]>();
    members.forEach((id)=>{const level=levels.get(id)??0;grouped.set(level,[...(grouped.get(level)||[]),id])});
    let componentBottom=componentY;
    [...grouped.entries()].sort(([a],[b])=>a-b).forEach(([level,ids])=>ids.sort((a,b)=>byId.get(a)!.data.label.localeCompare(byId.get(b)!.data.label)).forEach((id,index)=>{
      const x=120+level*horizontalGap;
      const nodeHeight=Math.max(120,60+(byId.get(id)!.data.interfaces?.length??4)*15);
      let y=componentY+index*verticalGap;
      let attempts=0;
      while(attempts++<200&&obstacles.some((obstacle)=>
        x<obstacle.x+obstacle.width+30&&x+160>obstacle.x-30&&
        y<obstacle.y+obstacle.height+30&&y+nodeHeight>obstacle.y-30)) y+=verticalGap;
      positions.set(id,{x,y});componentBottom=Math.max(componentBottom,y+nodeHeight);
    }));
    componentY=componentBottom+100;
  }
  return nodes.map((node)=>({...structuredClone(node),position:positions.get(node.id)!} as N));
}

export function alignSelectedNodes<
  N extends { id: string; position: { x: number; y: number } },
>(nodes:N[],selectedIds:ReadonlySet<string>,axis:"row"|"column"):N[] {
  const selected=nodes.filter((node)=>selectedIds.has(node.id));
  if(selected.length<2)return nodes.map((node)=>structuredClone(node));
  const coordinate=selected.reduce((sum,node)=>sum+(axis==="row"?node.position.y:node.position.x),0)/selected.length;
  return nodes.map((node)=>selectedIds.has(node.id)?({...structuredClone(node),position:{...node.position,
    ...(axis==="row"?{y:coordinate}:{x:coordinate})}} as N):structuredClone(node));
}
