import { api, ApiError, download, normalizeApiError, startArtifactDownload } from "@/lib/api";
import { vi } from "vitest";

vi.mock("@/lib/supabase", () => ({
  getSupabase: () => ({ auth: {
    getSession: vi.fn().mockResolvedValue({ data: { session: { access_token: "test-token" } } }),
    signOut: vi.fn(),
  } }),
}));

describe("authenticated API transport", () => {
  beforeEach(() => { process.env.NEXT_PUBLIC_FASTAPI_API_URL = "https://api.example.test"; });
  it("injects bearer and idempotency headers", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await api("/api/v2/execution/runs", { method: "POST", body: "{}", idempotencyKey: "key-1" });
    expect(fetchMock).toHaveBeenCalledWith("/_asre-api/api/v2/execution/runs", expect.any(Object));
    const request = fetchMock.mock.calls[0][1];
    expect(request.headers.get("Authorization")).toBe("Bearer test-token");
    expect(request.headers.get("Idempotency-Key")).toBe("key-1");
  });
  it("proxies artifact downloads while preserving authorization and disposition", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("artifact", { status: 200, headers: { "content-disposition": "attachment; filename=result.csv" } }));
    vi.stubGlobal("fetch", fetchMock);
    const artifact = await download("/api/simulations/run-1/export/csv");
    expect(fetchMock).toHaveBeenCalledWith("/_asre-api/api/simulations/run-1/export/csv", expect.any(Object));
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer test-token");
    expect(artifact.disposition).toBe("attachment; filename=result.csv");
  });
  it("starts non-empty artifact downloads through an attached temporary anchor and revokes the URL after click", () => {
    const createObjectURL = vi.fn(() => "blob:report");
    const revokeObjectURL = vi.fn();
    const appendChild = vi.spyOn(document.body, "appendChild");
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
    vi.useFakeTimers();
    try {
      expect(startArtifactDownload({ blob: new Blob(["report"]), disposition: "attachment; filename=backend-report.json" }, "fallback.json")).toBe("backend-report.json");
      const anchor = appendChild.mock.calls[0][0] as HTMLAnchorElement;
      expect(anchor.tagName).toBe("A");
      expect(anchor.href).toBe("blob:report");
      expect(anchor.download).toBe("backend-report.json");
      expect(click).toHaveBeenCalled();
      expect(anchor.isConnected).toBe(false);
      expect(revokeObjectURL).not.toHaveBeenCalled();
      vi.runAllTimers();
      expect(revokeObjectURL).toHaveBeenCalledWith("blob:report");
    } finally {
      vi.useRealTimers();
      vi.unstubAllGlobals();
      appendChild.mockRestore();
      click.mockRestore();
    }
  });
  it("rejects an empty artifact before attempting a browser download", () => {
    expect(() => startArtifactDownload({ blob: new Blob([]), disposition: null }, "fallback.json", "Report download failed. Please try again.")).toThrow("Report download failed. Please try again.");
  });
  it("preserves safe backend errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Invalid input", code: "INVALID_INPUT" }), { status: 422 })));
    try {
      await api("/api/test");
      throw new Error("Expected API failure");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).status).toBe(422);
      expect((error as ApiError).code).toBe("INVALID_INPUT");
    }
  });
  it("normalizes FastAPI strings, validation arrays, nested objects, and unknown bodies", () => {
    expect(normalizeApiError({ detail: "Base length must be greater than zero" })).toBe("Base length must be greater than zero");
    expect(normalizeApiError({ detail: [{ loc: ["body", "base_length_m"], msg: "Input should be greater than 0" }] })).toBe("base_length_m: Input should be greater than 0");
    expect(normalizeApiError({ detail: { boundary_conditions: { heat_source_w_m3: { message: "Must be non-negative" } } } })).toContain("Must be non-negative");
    expect(normalizeApiError({ detail: { type: "validation_error" } })).not.toBe("[object Object]");
  });
});
