import { render, screen } from "@testing-library/react";
import { vi } from "vitest";
import AuthenticatedScientificScopePage from "@/app/app/scientific-scope/page";
import { AppShell } from "@/components/app-shell";

vi.mock("next/navigation", () => ({ usePathname: () => "/app/scientific-scope", useRouter: () => ({ replace: vi.fn(), push: vi.fn() }) }));
vi.mock("@/components/auth-provider", () => ({ useAuth: () => ({ loading: false, session: { user: { email: "researcher@example.test" } } }) }));
vi.mock("@/lib/api", () => ({ api: vi.fn().mockResolvedValue({}) }));
vi.mock("@/lib/supabase", () => ({ getSupabase: () => ({ auth: { signOut: vi.fn() } }) }));

it("renders authenticated Scientific Scope inside the workspace shell", () => {
  render(<AppShell><AuthenticatedScientificScopePage /></AppShell>);
  expect(screen.getByRole("complementary", { name: "Research workspace navigation" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Boundaries before buttons." })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Scientific Scope" })).toHaveAttribute("href", "/app/scientific-scope");
});
