import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, vi } from "vitest";
import { AuthForm } from "@/components/auth-form";

const mocks = vi.hoisted(() => ({ replace: vi.fn(), signInWithPassword: vi.fn(), signUp: vi.fn() }));

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: mocks.replace }) }));
vi.mock("@/lib/supabase", () => ({ isSupabaseConfigured: () => true, getSupabase: () => ({ auth: { signInWithPassword: mocks.signInWithPassword, signUp: mocks.signUp } }) }));

describe("authentication form behavior", () => {
  beforeEach(() => { mocks.replace.mockReset(); mocks.signInWithPassword.mockReset(); mocks.signUp.mockReset(); window.history.pushState({}, "", "/auth/log-in?returnTo=/app/studies/study-1"); });

  it("preserves login and the validated workspace return target", async () => {
    mocks.signInWithPassword.mockResolvedValue({ error: null });
    render(<AuthForm mode="log-in" />);
    fireEvent.change(screen.getByLabelText("Email address"), { target: { value: "researcher@example.test" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "evidence-first" } });
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));
    await waitFor(() => expect(mocks.signInWithPassword).toHaveBeenCalledWith({ email: "researcher@example.test", password: "evidence-first" }));
    expect(mocks.replace).toHaveBeenCalledWith("/app/studies/study-1");
  });

  it("preserves client-side password validation", () => {
    render(<AuthForm mode="sign-up" />);
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "short" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Use at least 8 characters.");
    expect(mocks.signUp).not.toHaveBeenCalled();
  });
});
