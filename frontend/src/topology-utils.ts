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
