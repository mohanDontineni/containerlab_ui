export function interfaceFromHandle(value: string | null | undefined): string {
  return value?.split(":").slice(1).join(":") || "";
}

export function interfaceIsAvailable(
  handle: string,
  usedHandles: ReadonlySet<string>,
): boolean {
  return interfaceFromHandle(handle) !== "" && !usedHandles.has(handle);
}
