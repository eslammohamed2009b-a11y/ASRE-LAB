import { api, ApiError } from "@/lib/api";
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
    const request = fetchMock.mock.calls[0][1];
    expect(request.headers.get("Authorization")).toBe("Bearer test-token");
    expect(request.headers.get("Idempotency-Key")).toBe("key-1");
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
});
