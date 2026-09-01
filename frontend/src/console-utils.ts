export function requestedConsoleDevice(eventOrigin: string, appOrigin: string, data: unknown): string {
  if (eventOrigin !== appOrigin || !data || typeof data !== "object") return "";
  const message = data as { type?: unknown; deviceId?: unknown };
  return message.type === "open-device-console" && typeof message.deviceId === "string" ? message.deviceId : "";
}
