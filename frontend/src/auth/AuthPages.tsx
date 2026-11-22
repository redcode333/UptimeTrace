import { SignIn, SignUp } from "@clerk/react";
import type { ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { BrandMark } from "../components/brand/BrandMark";
import { clerkAppearance } from "./clerkAppearance";
import { safeReturnPath } from "./redirect";

/** Clerk's own logo is hidden on these pages; the UptimeTrace mark above replaces it. The shared appearance stays unchanged for the account menu. */
const authAppearance = {
  ...clerkAppearance,
  layout: { logoPlacement: "none" },
  elements: { rootBox: { width: "100%" }, card: { width: "100%" } },
} as const;

function Frame({ children }: { children: ReactNode }) {
  return (
    <div className="centered auth">
      <div className="auth-col">
        <BrandMark />
        <p className="dim">Test your alerts before the outage does.</p>
        {children}
        <p className="dim small auth-note">Sign-in, passwords and account recovery are handled by Clerk. UptimeTrace never receives or stores a password.</p>
      </div>
    </div>
  );
}

/** Email and password, Google, and "Forgot password?" are all part of Clerk's sign-in flow, configured in the Clerk dashboard. */
export function SignInPage() {
  const [params] = useSearchParams();
  const expired = params.get("reason") === "session_expired";
  return (
    <Frame>
      {expired && <p className="banner notice" role="status">Your session ended, so you were signed out. Sign in again to continue.</p>}
      <SignIn routing="path" path="/sign-in" signUpUrl="/sign-up" appearance={authAppearance} />
    </Frame>
  );
}

/** New accounts start in onboarding unless they were sent to a specific page first. */
export function SignUpPage() {
  const [params] = useSearchParams();
  const requested = params.get("redirect_url");
  const back = safeReturnPath(requested, "/onboarding");
  return (
    <Frame>
      <SignUp routing="path" path="/sign-up" signInUrl="/sign-in" fallbackRedirectUrl={back} appearance={authAppearance} />
    </Frame>
  );
}
