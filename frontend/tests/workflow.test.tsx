import { render, screen, waitFor } from "@testing-library/react";
import { StudyWorkflow } from "@/components/study-workflow";
import { vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: vi.fn((path: string) => Promise.resolve(path.includes("capabilities") ? { solvers: [] } : [])),
  download: vi.fn(),
  ApiError: class extends Error {},
}));

describe("engineering workflow", () => {
  it("renders all seven stages and no unsupported controls", async () => {
    render(<StudyWorkflow />);
    for (const stage of ["Design", "Physics", "Validation", "Execution", "Evidence", "Decision", "Report"]) {
      expect(screen.getByRole("button", { name: stage })).toBeInTheDocument();
    }
    await waitFor(() => expect(screen.queryByText(/radiative|pause stage|cpu diagnostics|account settings/i)).not.toBeInTheDocument());
  });
});
