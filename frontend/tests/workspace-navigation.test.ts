import { describe, expect, it } from "vitest";
import { isWorkspaceNavigationActive, workspaceNavigation, workspacePageContext } from "@/components/app-shell";

describe("workspace route context", () => {
  it("keeps Research Studies active for persisted study resources", () => {
    for (const pathname of [
      "/app/dashboard", "/app/studies/study-1", "/app/simulations/run-1", "/app/jobs/job-1",
      "/app/attempts/attempt-1", "/app/decisions/decision-1", "/app/reasoning/reasoning-1",
      "/app/reports/report-1", "/app/manifests/manifest-1",
    ]) {
      expect(isWorkspaceNavigationActive(pathname, "/app/dashboard")).toBe(true);
    }
    expect(isWorkspaceNavigationActive("/app/studies/new", "/app/dashboard")).toBe(false);
    expect(isWorkspaceNavigationActive("/app/studies/new", "/app/studies/new")).toBe(true);
  });

  it("labels persisted resources with their actual topbar context", () => {
    expect(workspacePageContext("/app/studies/study-1")).toBe("Research study");
    expect(workspacePageContext("/app/simulations/run-1")).toBe("Simulation result");
    expect(workspacePageContext("/app/jobs/job-1")).toBe("Generation job");
    expect(workspacePageContext("/app/attempts/attempt-1")).toBe("Execution attempt");
    expect(workspacePageContext("/app/decisions/decision-1")).toBe("Engineering decision");
    expect(workspacePageContext("/app/reasoning/reasoning-1")).toBe("Scientific reasoning");
    expect(workspacePageContext("/app/reports/report-1")).toBe("Reproducible report");
    expect(workspacePageContext("/app/manifests/manifest-1")).toBe("Execution manifest");
    expect(workspacePageContext("/app/scientific-scope")).toBe("Scientific scope");
  });

  it("keeps Scientific Scope inside the authenticated workspace", () => {
    expect(workspaceNavigation.find((item) => item.label === "Scientific Scope")?.href).toBe("/app/scientific-scope");
    expect(isWorkspaceNavigationActive("/app/scientific-scope", "/app/scientific-scope")).toBe(true);
  });

  it("keeps Documentation inside the authenticated workspace", () => {
    expect(workspaceNavigation.find((item) => item.label === "Documentation")?.href).toBe("/app/docs");
    expect(isWorkspaceNavigationActive("/app/docs", "/app/docs")).toBe(true);
    expect(workspacePageContext("/app/docs")).toBe("Documentation");
    expect(isWorkspaceNavigationActive("/docs", "/app/docs")).toBe(false);
  });
});
