import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, vi } from "vitest";
import { AuthForm } from "@/components/auth-form";

const mocks = vi.hoisted(() => ({ replace: vi.fn(), signInAnonymously: vi.fn() }));

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: mocks.replace }) }));
vi.mock("@/lib/supabase", () => ({ isSupabaseConfigured: () => true, getSupabase: () => ({ auth: { signInAnonymously: mocks.signInAnonymously } }) }));

describe("guest access behavior", () => {
  beforeEach(() => { mocks.replace.mockReset(); mocks.signInAnonymously.mockReset(); window.history.pushState({}, "", "/auth/log-in?returnTo=/app/studies/study-1"); });

  it("creates an anonymous session and preserves the validated workspace return target", async () => {
    mocks.signInAnonymously.mockResolvedValue({ error: null });
    render(<AuthForm />);
    fireEvent.click(screen.getByRole("button", { name: "Start ASRE-Lab" }));
    await waitFor(() => expect(mocks.signInAnonymously).toHaveBeenCalledOnce());
    expect(mocks.replace).toHaveBeenCalledWith("/app/studies/study-1");
  });

  it("shows anonymous sign-in failures without redirecting", async () => {
    mocks.signInAnonymously.mockResolvedValue({ error: new Error("Anonymous sign-ins are disabled") });
    render(<AuthForm />);
    fireEvent.click(screen.getByRole("button", { name: "Start ASRE-Lab" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Anonymous sign-ins are disabled");
    expect(mocks.replace).not.toHaveBeenCalled();
  });
});
