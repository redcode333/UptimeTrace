import { render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it } from "vitest";
import { installLandingStubs } from "../../test/landingStubs";
import { LandingPage } from "./LandingPage";

// jsdom has no WebGL, observers, matchMedia or 2D canvas. The runtime is written to fall back in each case; these stubs let it run.
beforeAll(() => installLandingStubs());

afterEach(() => {
  localStorage.clear();
});

describe("landing page", () => {
  it("renders the hero headline and the two calls to action", () => {
    const { unmount } = render(<LandingPage />);
    expect(screen.getAllByText(/Test your alerts before/i).length).toBeGreaterThan(0);
    const getStarted = screen.getAllByRole("link", { name: /Get started/i })[0];
    expect(getStarted).toHaveAttribute("href", "/onboarding");
    const signIn = screen.getByRole("link", { name: "Sign in" });
    expect(signIn).toHaveAttribute("href", "/sign-in");
    unmount();
  });

  it("states the MCP connection in generic terms and names only real MCP tools", () => {
    const { container, unmount } = render(<LandingPage />);
    expect(screen.getByText("any MCP client")).toBeInTheDocument();
    const text = container.textContent ?? "";
    expect(text).toContain("list_hosts");
    expect(text).toContain("get_host_health");
    expect(text).toContain("get_host_metrics");
    unmount();
  });

  it("makes no claims for capabilities the product does not have", () => {
    const { container, unmount } = render(<LandingPage />);
    const html = container.innerHTML.toLowerCase();
    for (const claim of ["pagerduty", "discord", "intelligence lab", "docker", "opentelemetry", "gemini", "windsurf", "copilot",
      "get_alerts", "drill_results", "telemetry loss", "network latency", "throwaway container"]) {
      expect(html, claim).not.toContain(claim);
    }
    unmount();
  });

  it("labels the playground as an in-browser demo", () => {
    const { container, unmount } = render(<LandingPage />);
    expect(container.querySelector("#play")).not.toBeNull();
    expect(screen.getByText(/in-browser demo/i)).toBeInTheDocument();
    unmount();
  });

  it("starts and stops cleanly, restoring the page title", () => {
    const before = document.title;
    const { unmount } = render(<LandingPage />);
    expect(document.title).toContain("UptimeTrace");
    expect(() => unmount()).not.toThrow();
    expect(document.title).toBe(before);
  });
});
