import { render, screen } from "@testing-library/react";
import { vi } from "vitest";
import WorkspaceDocumentationPage from "@/app/app/docs/page";
import { AppShell } from "@/components/app-shell";

vi.mock("next/navigation", () => ({ usePathname: () => "/app/docs", useRouter: () => ({ replace: vi.fn(), push: vi.fn() }) }));
vi.mock("@/components/auth-provider", () => ({ useAuth: () => ({ loading: false, session: { user: { email: "researcher@example.test" } } }) }));
vi.mock("@/lib/api", () => ({ api: vi.fn().mockResolvedValue({}) }));
vi.mock("@/lib/supabase", () => ({ getSupabase: () => ({ auth: { signOut: vi.fn() } }) }));

it("renders Documentation inside AppShell with an active workspace route", () => {
  render(<AppShell><WorkspaceDocumentationPage /></AppShell>);
  expect(screen.getByRole("complementary", { name: "Research workspace navigation" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Documentation" })).toHaveAttribute("href", "/app/docs");
  expect(screen.getByRole("link", { name: "Documentation" })).toHaveAttribute("data-active", "true");
  expect(screen.getByRole("heading", { name: "From a research question to a traceable engineering record" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "What ASRE-Lab does" })).toBeInTheDocument();
  expect(screen.getByText("CAD generation ≠ physics simulation")).toBeInTheDocument();
  expect(screen.getByText("Simulation convergence ≠ spatial refinement")).toBeInTheDocument();
  expect(screen.getByText("Human Decision")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Back to Studies" })).toHaveAttribute("href", "/app/dashboard");
});
