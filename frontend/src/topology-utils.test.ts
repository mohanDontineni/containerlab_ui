import { describe, expect, it } from "vitest";

import { interfaceFromHandle, interfaceIsAvailable } from "./topology-utils";

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
});
