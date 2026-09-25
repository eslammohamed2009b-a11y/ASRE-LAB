import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { CommandPalette } from "@/components/ui/command-palette";
import { workspaceNavigation } from "@/components/app-shell";

const commands = [
  { label: "Research Studies", description: "Review persisted studies", href: "/app/dashboard", shortcut: "1" },
  { label: "Scientific Scope", description: "Review supported physics", href: "/scientific-scope", shortcut: "4" },
];

describe("workspace command palette", () => {
  it("opens with Ctrl+K and navigates the selected command with the keyboard", async () => {
    const onNavigate = vi.fn();
    render(<CommandPalette commands={commands} onNavigate={onNavigate} />);

    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(screen.getByRole("dialog", { name: "Workspace command palette" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Search workspace commands" })).toHaveFocus());

    fireEvent.keyDown(window, { key: "ArrowDown" });
    fireEvent.keyDown(window, { key: "Enter" });
    expect(onNavigate).toHaveBeenCalledWith("/scientific-scope");
  });

  it("filters destinations and closes with Escape", () => {
    render(<CommandPalette commands={commands} onNavigate={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Open workspace command palette" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Search workspace commands" }), { target: { value: "scope" } });
    expect(screen.queryByText("Research Studies")).not.toBeInTheDocument();
    expect(screen.getByText("Scientific Scope")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Workspace command palette" })).not.toBeInTheDocument();
  });

  it("traps Tab focus while open and restores focus to its trigger when closed", async () => {
    render(<CommandPalette commands={commands} onNavigate={vi.fn()} />);
    const trigger = screen.getByRole("button", { name: "Open workspace command palette" });
    trigger.focus();
    fireEvent.click(trigger);

    const input = screen.getByRole("textbox", { name: "Search workspace commands" });
    const lastResult = screen.getByRole("option", { name: /Scientific Scope/ });
    await waitFor(() => expect(input).toHaveFocus());

    fireEvent.keyDown(window, { key: "Tab", shiftKey: true });
    expect(lastResult).toHaveFocus();
    fireEvent.keyDown(window, { key: "Tab" });
    expect(input).toHaveFocus();

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Open workspace command palette" })).toHaveFocus());
  });

  it("opens workspace Documentation without leaving the app route", () => {
    const onNavigate = vi.fn();
    render(<CommandPalette commands={workspaceNavigation} onNavigate={onNavigate} />);
    fireEvent.click(screen.getByRole("button", { name: "Open workspace command palette" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Search workspace commands" }), { target: { value: "documentation" } });
    fireEvent.click(screen.getByRole("option", { name: /Documentation/ }));
    expect(onNavigate).toHaveBeenCalledWith("/app/docs");
  });
});
