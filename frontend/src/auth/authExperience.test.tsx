import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import { installLandingStubs } from "../test/landingStubs";
import { renderApp } from "../test/harness";
import { UserMenu } from "../components/shell/UserMenu";


const clerk = vi.hoisted(() => ({
  openUserProfile: vi.fn(),
  signOut: vi.fn(),
  user: {
    fullName: "Ada Lovelace",
    primaryEmailAddress: { emailAddress: "ada@example.test" },
    createdAt: new Date("2026-11-01T09:00:00Z"),
  } as Record<string, unknown> | null,
}));

vi.mock("@clerk/react", () => ({
  useAuth: () => ({ isLoaded: true, isSignedIn: false }),
  useClerk: () => ({ openUserProfile: clerk.openUserProfile, signOut: clerk.signOut }),
  useUser: () => ({ user: clerk.user }),
  SignIn: () => <div>CLERK SIGN IN</div>,
  SignUp: () => <div>CLERK SIGN UP</div>,
}));

const { SignInPage } = await import("./AuthPages");

describe("Sign-in after an expired session", () => {
  it("says the session ended when the user is sent back to sign in because of it", () => {
    installLandingStubs();
    renderApp(
      <Routes><Route path="/sign-in" element={<SignInPage />} /></Routes>,
      { route: "/sign-in?reason=session_expired&redirect_url=%2Fhosts" },
    );
    expect(screen.getByRole("status")).toHaveTextContent("Your session ended, so you were signed out.");
    expect(screen.getByText("CLERK SIGN IN")).toBeInTheDocument();
  });

  it("shows no session notice on an ordinary sign-in", () => {
    installLandingStubs();
    renderApp(<Routes><Route path="/sign-in" element={<SignInPage />} /></Routes>, { route: "/sign-in" });
    expect(screen.queryByText(/Your session ended/)).toBeNull();
  });

  it("says that Clerk, not UptimeTrace, handles passwords", () => {
    installLandingStubs();
    renderApp(<Routes><Route path="/sign-in" element={<SignInPage />} /></Routes>, { route: "/sign-in" });
    expect(screen.getByText(/UptimeTrace never receives or stores a password/)).toBeInTheDocument();
  });
});

describe("Account menu", () => {
  it("shows the signed-in account's name, email and creation date from Clerk", () => {
    clerk.user = { fullName: "Ada Lovelace", primaryEmailAddress: { emailAddress: "ada@example.test" }, createdAt: new Date("2026-11-01T09:00:00Z") };
    renderApp(<UserMenu />);
    fireEvent.click(screen.getByRole("button", { name: "Account menu" }));
    expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();
    expect(screen.getByText("ada@example.test")).toBeInTheDocument();
    expect(screen.getByText(/Account created/)).toBeInTheDocument();
  });

  it("opens Clerk's own account profile from Manage account", () => {
    clerk.openUserProfile.mockClear();
    clerk.user = { fullName: "Ada Lovelace", primaryEmailAddress: { emailAddress: "ada@example.test" }, createdAt: null };
    renderApp(<UserMenu />);
    fireEvent.click(screen.getByRole("button", { name: "Account menu" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Manage account" }));
    expect(clerk.openUserProfile).toHaveBeenCalledTimes(1);
  });

  it("does not invent an email for an account that has none", () => {
    clerk.user = { fullName: null, primaryEmailAddress: null, createdAt: null };
    renderApp(<UserMenu />);
    fireEvent.click(screen.getByRole("button", { name: "Account menu" }));
    expect(screen.getByText("No email on this account")).toBeInTheDocument();
    expect(screen.getByText("Signed in")).toBeInTheDocument();
  });
});
