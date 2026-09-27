import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { ResourceDetail } from "@/components/resource-detail";
import { api, download, startArtifactDownload } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error { status = 0; },
  api: vi.fn((path: string) => path.endsWith("/fields")
    ? Promise.resolve([{ id: "field-1", field_name: "temperature" }])
    : Promise.resolve({ id: "simulation-1", status: "completed" })),
  download: vi.fn(),
  startArtifactDownload: vi.fn(),
}));

beforeEach(() => { vi.mocked(api).mockClear(); vi.mocked(download).mockReset(); vi.mocked(startArtifactDownload).mockReset(); });

it("keeps the private field download action available and reports its failure", async () => {
  vi.mocked(download).mockRejectedValueOnce(new Error("The artifact is unavailable."));
  render(<ResourceDetail kind="simulation" id="simulation-1" />);
  const button = await screen.findByRole("button", { name: "Download temperature NPZ" });
  fireEvent.click(button);
  expect(await screen.findByRole("alert")).toHaveTextContent("The artifact is unavailable.");
  expect(button).toBeEnabled();
  await waitFor(() => expect(download).toHaveBeenCalledWith("/api/simulations/simulation-1/fields/field-1/download"));
});
