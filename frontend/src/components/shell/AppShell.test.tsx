import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "../../test/harness";
import { AppShell } from "./AppShell";

vi.mock("@clerk/react", () => ({
  useUser: () => ({ user: { fullName: "Ada Lovelace", primaryEmailAddress: { emailAddress: "ada@example.com" } } }),
  useClerk: () => ({ signOut: vi.fn() }),
}));

function Where() {
  const l = useLocation();
  return <div data-testid="where">{l.pathname + l.search}</div>;
}
function shell() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="*" element={<Where />} />
      </Route>
    </Routes>
  );
}

describe("AppShell", () => {
  beforeEach(() => window.localStorage.clear());

  it("renders the approved navigation and marks pages that have no backend as not built", () => {
    renderApp(shell());
    const nav = screen.getByRole("navigation", { name: "Main" });
    for (const label of ["Overview", "Hosts", "Metrics Explorer", "Dependencies", "Risk Outlook", "Workspace & members"]) {
      expect(within(nav).getByRole("link", { name: new RegExp(label.replace("&", "&")) })).toBeInTheDocument();
    }
    expect(within(nav).getByText("EXP")).toBeInTheDocument();
  });

  it("does not pretend search, notifications or workspace switching work", () => {
    renderApp(shell());
    expect(screen.getByRole("button", { name: /Search/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Notifications" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Workspace/ })).toBeDisabled();
  });

  it("shows real host counts per source and keeps the scope in the URL", async () => {
    const user = userEvent.setup();
    renderApp(shell(), { route: "/hosts" });
    const live = await screen.findByRole("button", { name: /LIVE\s*3/ });
    expect(screen.getByRole("button", { name: /SYNTHETIC\s*31/ })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: /SYNTHETIC/ }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/hosts?source=live%2Creplay"));
    expect(screen.getByRole("button", { name: /SYNTHETIC/ })).toHaveAttribute("aria-pressed", "false");
    expect(live).toHaveAttribute("aria-pressed", "true");
  });

  it("refuses to turn off the last selected source", async () => {
    const user = userEvent.setup();
    renderApp(shell(), { route: "/?source=replay" });
    const replay = await screen.findByRole("button", { name: /REPLAY/ });
    expect(replay).toHaveAttribute("aria-disabled", "true");
    await user.click(replay);
    expect(screen.getByTestId("where")).toHaveTextContent("/?source=replay");
    expect(replay).toHaveAttribute("aria-pressed", "true");
  });

  it("keeps scope and time range when navigating, and shows the user's name in the account menu only", async () => {
    const user = userEvent.setup();
    renderApp(shell(), { route: "/?source=live&range=24h" });
    await user.click(within(screen.getByRole("navigation", { name: "Main" })).getByRole("link", { name: /^Hosts/ }));
    expect(screen.getByTestId("where")).toHaveTextContent("/hosts?source=live&range=24h");
    await user.click(screen.getByRole("button", { name: "Account menu" }));
    expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Sign out" })).toBeInTheDocument();
  });

  it("opens the mobile drawer from the menu button and closes it with Escape", async () => {
    const user = userEvent.setup();
    renderApp(shell());
    const sidebar = document.getElementById("app-sidebar")!;
    expect(sidebar).not.toHaveClass("open");
    await user.click(screen.getByRole("button", { name: "Open menu" }));
    expect(sidebar).toHaveClass("open");
    await user.keyboard("{Escape}");
    expect(sidebar).not.toHaveClass("open");
  });

  it("collapses the sidebar and remembers it", async () => {
    const user = userEvent.setup();
    renderApp(shell());
    await user.click(screen.getByRole("button", { name: /Collapse/ }));
    expect(document.getElementById("app-sidebar")).toHaveClass("collapsed");
    expect(window.localStorage.getItem("uptimetrace.sidebar.collapsed")).toBe("1");
  });
});
