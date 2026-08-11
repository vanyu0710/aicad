import { describe, expect, it } from "vitest";
import { resolveApiRoot, resolveWsRoot } from "./api";

describe("same-origin address resolution", () => {
  it("defaults API root to the current origin", () => {
    expect(resolveApiRoot({})).toBe("");
    expect(resolveApiRoot({ VITE_API_ROOT: "http://127.0.0.1:9000" })).toBe("http://127.0.0.1:9000");
  });

  it("prefers an explicit WebSocket override", () => {
    expect(resolveWsRoot({ VITE_WS_ROOT: "wss://example.test" })).toBe("wss://example.test");
  });

  it("derives WebSocket root from an API root", () => {
    expect(resolveWsRoot({}, "http://127.0.0.1:8001")).toBe("ws://127.0.0.1:8001");
  });

  it("derives same-origin WebSocket root from window.location", () => {
    const root = resolveWsRoot({}, "");
    expect(root.startsWith("ws://")).toBe(true);
    expect(root.endsWith("/")).toBe(false);
  });
});
