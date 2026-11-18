import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { fakeApi, renderApp } from "../test/harness";
import { RequireAuth } from "./RequireAuth";

const clerk = vi.hoisted(() => ({ auth: { isLoaded: true, isSignedIn: true }, signOut: vi.fn() }));
vi.mock("@clerk/react", () => ({
  useAuth: () => clerk.auth,
  useClerk: () => ({ signOut: clerk.signOut }),
}));

function tree() {
  return (
    <Routes>
      <Route path="/sign-in" element={<div>SIGN IN PAGE</div>} />
      <Route element={<RequireAuth />}>
        <Route path="/" element={<div>PROTECTED APP</div>} />
      </Route>
    </Routes>
  );
}

describe("RequireAuth (the authentication boundary)", () => {
  beforeEach(() => { clerk.auth = { isLoaded: true, isSignedIn: true }; clerk.signOut.mockClear(); });

  it("waits while Clerk is loading and shows nothing protected", () => {
    clerk.auth = { isLoaded: false, isSignedIn: false };
    renderApp(tree());
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByText("PROTECTED APP")).not.toBeInTheDocument();
    expect(screen.queryByText("SIGN IN PAGE")).not.toBeInTheDocument();
  });

  it("sends a signed-out visitor to sign-in and never asks the API anything", () => {
    clerk.auth = { isLoaded: true, isSignedIn: false };
    const me = vi.fn();
    renderApp(tree(), { api: fakeApi({ me }) });
    expect(screen.getByText("SIGN IN PAGE")).toBeInTheDocument();
    expect(me).not.toHaveBeenCalled();
  });

  it("renders the app only when the API says this account is authorized", async () => {
    renderApp(tree());
    expect(await screen.findByText("PROTECTED APP")).toBeInTheDocument();
  });

  it("shows a signed-in but non-allowlisted user their id and no app content", async () => {
    renderApp(tree(), { api: fakeApi({ me: async () => ({ user_id: "user_stranger_9", authorized: false }) }) });
    expect(await screen.findByText("NOT AUTHORIZED")).toBeInTheDocument();
    expect(screen.getByTestId("user-id")).toHaveTextContent("user_stranger_9");
    expect(screen.queryByText("PROTECTED APP")).not.toBeInTheDocument();
  });

  it("shows an error state (not the app, not a false 'unauthorized') when the API is unreachable", async () => {
    renderApp(tree(), { api: fakeApi({ me: async () => { throw new ApiError(0, "network_error", "Could not reach the UptimeTrace API."); } }) });
    expect(await screen.findByText("Could not reach the API")).toBeInTheDocument();
    expect(screen.queryByText("PROTECTED APP")).not.toBeInTheDocument();
    expect(screen.queryByText("NOT AUTHORIZED")).not.toBeInTheDocument();
  });
});
