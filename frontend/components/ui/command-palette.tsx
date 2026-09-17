"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export type WorkspaceCommand = {
  label: string;
  description: string;
  href: string;
  shortcut?: string;
};

type CommandPaletteProps = {
  commands: WorkspaceCommand[];
  onNavigate: (href: string) => void;
};

function isEditableTarget(target: EventTarget | null) {
  const element = target as HTMLElement | null;
  return Boolean(element?.closest("input, textarea, select, [contenteditable='true']"));
}

export function CommandPalette({ commands, onNavigate }: CommandPaletteProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [isMac, setIsMac] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const results = useMemo(() => {
    const term = query.trim().toLocaleLowerCase();
    if (!term) return commands;
    return commands.filter((command) =>
      `${command.label} ${command.description}`.toLocaleLowerCase().includes(term),
    );
  }, [commands, query]);

  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
    setSelectedIndex(0);
  }, []);

  const activate = useCallback((command: WorkspaceCommand | undefined) => {
    if (!command) return;
    close();
    onNavigate(command.href);
  }, [close, onNavigate]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLocaleLowerCase() === "k") {
        event.preventDefault();
        setOpen((value) => !value);
        return;
      }
      if (!open) return;
      if (event.key === "Escape") {
        event.preventDefault();
        close();
      } else if (event.key === "ArrowDown") {
        event.preventDefault();
        setSelectedIndex((index) => Math.min(index + 1, Math.max(results.length - 1, 0)));
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        setSelectedIndex((index) => Math.max(index - 1, 0));
      } else if (event.key === "Enter") {
        event.preventDefault();
        activate(results[selectedIndex]);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [activate, close, open, results, selectedIndex]);

  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  useEffect(() => {
    if (open) window.requestAnimationFrame(() => inputRef.current?.focus());
  }, [open]);

  useEffect(() => {
    setIsMac(/mac/i.test(navigator.platform));
  }, []);

  if (!open) {
    return (
      <button
        type="button"
        className="command-trigger"
        onClick={() => setOpen(true)}
        aria-label="Open workspace command palette"
      >
        <span>Search workspace</span>
        <kbd><span className="command-key-symbol">{isMac ? "⌘" : "Ctrl"}</span><span className="command-key-letter">K</span></kbd>
      </button>
    );
  }

  return (
    <div className="command-overlay" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) close();
    }}>
      <section className="command-palette" role="dialog" aria-modal="true" aria-label="Workspace command palette">
        <div className="command-search-row">
          <span className="command-search-mark" aria-hidden="true">⌕</span>
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search research workspace"
            aria-label="Search workspace commands"
            onKeyDown={(event) => {
              if (isEditableTarget(event.target) && event.key === "Tab") close();
            }}
          />
          <kbd>Esc</kbd>
        </div>
        <div className="command-list" role="listbox" aria-label="Workspace destinations">
          {results.length ? results.map((command, index) => (
            <button
              type="button"
              key={command.href}
              role="option"
              aria-selected={index === selectedIndex}
              className="command-row"
              data-selected={index === selectedIndex}
              onMouseEnter={() => setSelectedIndex(index)}
              onClick={() => activate(command)}
            >
              <span className="command-row-glyph" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
              <span className="command-row-copy"><strong>{command.label}</strong><small>{command.description}</small></span>
              {command.shortcut && <kbd>{command.shortcut}</kbd>}
            </button>
          )) : <p className="command-empty">No workspace destination matches “{query}”.</p>}
        </div>
        <footer className="command-footer"><span><kbd>↑</kbd><kbd>↓</kbd> Navigate</span><span><kbd>↵</kbd> Open</span></footer>
      </section>
    </div>
  );
}
