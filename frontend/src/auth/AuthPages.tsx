import { SignIn, SignUp } from "@clerk/react";
import type { ReactNode } from "react";
import { clerkAppearance } from "./clerkAppearance";

function Frame({ children }: { children: ReactNode }) {
  return (
    <div className="centered auth">
      <div className="auth-col">
        <div className="logo">
          <i />
          <span>UPTIME<b>TRACE</b></span>
        </div>
        <p className="dim">Trace the signals. Stay ahead of downtime.</p>
        {children}
      </div>
    </div>
  );
}

/** Email and password, Google, and "Forgot password?" are all part of Clerk's sign-in flow, configured in the Clerk dashboard. */
export function SignInPage() {
  return (
    <Frame>
      <SignIn routing="path" path="/sign-in" signUpUrl="/sign-up" appearance={clerkAppearance} />
    </Frame>
  );
}

export function SignUpPage() {
  return (
    <Frame>
      <SignUp routing="path" path="/sign-up" signInUrl="/sign-in" appearance={clerkAppearance} />
    </Frame>
  );
}
