import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { installLandingStubs } from "../test/landingStubs";
import { RequireAuth } from "./RequireAuth";
import { safeReturnPath, signInUrlFor } from "./redirect";

const clerk = vi.hoisted(() => ({ auth: { isLoaded: true, isSignedIn: false }, signInProps: {} as Record<string, unknown>, signUpProps: {} as Record<string, unknown> }));
vi.mock("@clerk/react", () => ({
  useAuth: () => clerk.auth,
  useClerk: () => ({ signOut: vi.fn() }),
  SignIn: (props: Record<string, unknown>) => {
    clerk.signInProps = props;
    return <div>CLERK SIGN IN</div>;
  },
  SignUp: (props: Record<string, unknown>) => {
    clerk.signUpProps = props;
    return <div>CLERK SIGN UP</div>;
  },
}));

// imported after the mock so the auth pages pick up the mocked Clerk components
const { SignInPage, SignUpPage } = await import("./AuthPages");

function WhereAmI() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname + location.search}</div>;
}

function app(initial: string) {
  return (
    <MemoryRouter initialEntries={[initial]}>
      <WhereAmI />
      <Routes>
        <Route path="/sign-in" element={<SignInPage />} />
        <Route path="/sign-up" element={<SignUpPage />} />
        <Route element={<RequireAuth />}>
          <Route path="/" element={<div>CONSOLE HOME</div>} />
          <Route path="/onboarding" element={<div>ONBOARDING</div>} />
        </Route>
      </Routes>
    </MemoryRouter>
  );
}

beforeAll(() => installLandingStubs());

describe("return paths", () => {
  it("accepts same-origin paths and nothing else", () => {
    expect(safeReturnPath("/onboarding")).toBe("/onboarding");
    expect(safeReturnPath("/hosts/abc?x=1")).toBe("/hosts/abc?x=1");
    expect(safeReturnPath("https://evil.example/")).toBe("/");
    expect(safeReturnPath("//evil.example")).toBe("/");
    expect(safeReturnPath("/\\evil.example")).toBe("/");
    expect(safeReturnPath("/sign-in")).toBe("/");
    expect(safeReturnPath(null, "/onboarding")).toBe("/onboarding");
  });

  it("encodes the requested page into the sign-in URL", () => {
    expect(signInUrlFor("/alerts/a1?w=5")).toBe("/sign-in?redirect_url=%2Falerts%2Fa1%3Fw%3D5");
  });
});

describe("public entry and sign-in return", () => {
  it("shows the public landing page at the root when signed out", () => {
    clerk.auth = { isLoaded: true, isSignedIn: false };
    render(app("/"));
    expect(screen.getAllByText(/Test your alerts before/i).length).toBeGreaterThan(0);
    expect(screen.queryByText("CONSOLE HOME")).toBeNull();
    expect(screen.getByTestId("location")).toHaveTextContent("/");
  });

  it("sends a signed-out visitor to a protected page through sign-in with the return path kept", () => {
    clerk.auth = { isLoaded: true, isSignedIn: false };
    render(app("/onboarding"));
    expect(screen.getByTestId("location").textContent).toBe("/sign-in?redirect_url=%2Fonboarding");
    expect(screen.getByText("CLERK SIGN IN")).toBeInTheDocument();
  });

  it("does not pass a fallback redirect to the sign-in component, so Clerk handles redirect_url itself", () => {
    clerk.auth = { isLoaded: true, isSignedIn: false };
    render(app("/sign-in?redirect_url=%2Fonboarding"));
    expect(clerk.signInProps).not.toHaveProperty("fallbackRedirectUrl");
  });

  it("sends a new account to onboarding unless it was asked for a page first", () => {
    clerk.auth = { isLoaded: true, isSignedIn: false };
    render(app("/sign-up"));
    expect(clerk.signUpProps.fallbackRedirectUrl).toBe("/onboarding");
    render(app("/sign-up?redirect_url=%2Fhosts"));
    expect(clerk.signUpProps.fallbackRedirectUrl).toBe("/hosts");
  });
});
