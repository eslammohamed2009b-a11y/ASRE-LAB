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
  expect(screen.getByRole("heading", { name: "Engineering workflow reference" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Back to Studies" })).toHaveAttribute("href", "/app/dashboard");
});
