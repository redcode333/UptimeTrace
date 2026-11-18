import { useAuth, useClerk } from "@clerk/react";
import { useState } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useMe } from "../api/hooks";
import { ErrorState, Loading } from "../components/states/States";

/**
 * The authentication boundary of the app:
 *   1. not loaded yet  -> wait;  2. signed out -> /sign-in (and back afterwards);
 *   3. signed in -> ask the API who we are (/api/v1/me); 4. not on the backend allowlist -> a dead end that shows the
 *      user id (so the owner can allow it) and offers sign out; 5. otherwise render the app.
 * The browser only ever asks; the API decides. Nothing here grants access.
 */
export function RequireAuth() {
  const { isLoaded, isSignedIn } = useAuth();
  const location = useLocation();
  if (!isLoaded) return <div className="centered"><Loading label="Checking your session" /></div>;
  if (!isSignedIn) return <Navigate to="/sign-in" replace state={{ from: location.pathname + location.search }} />;
  return <AuthorizedGate />;
}

function AuthorizedGate() {
  const me = useMe();
  const clerk = useClerk();
  const [copied, setCopied] = useState(false);

  if (me.isPending) return <div className="centered"><Loading label="Checking access" /></div>;
  if (me.isError) {
    return (
      <div className="centered">
        <div className="card">
          <ErrorState error={me.error} onRetry={() => me.refetch()} />
          <button type="button" className="btn" onClick={() => clerk.signOut({ redirectUrl: "/sign-in" })}>Sign out</button>
        </div>
      </div>
    );
  }
  if (!me.data.authorized) {
    return (
      <div className="centered">
        <div className="card" role="alert">
          <span className="flag crit">NOT AUTHORIZED</span>
          <h1>This account cannot view the workspace yet</h1>
          <p>
            You are signed in, but your account is not on this deployment&apos;s allowlist. Ask the workspace owner to add
            your user id to <span className="mono">UPTIMETRACE_ALLOWED_USER_IDS</span> on the backend.
          </p>
          <div className="idrow">
            <code className="mono" data-testid="user-id">{me.data.user_id}</code>
            <button type="button" className="btn" onClick={async () => {
              try { await navigator.clipboard.writeText(me.data.user_id); setCopied(true); } catch { /* user can select the text */ }
            }}>
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          <button type="button" className="btn primary" onClick={() => clerk.signOut({ redirectUrl: "/sign-in" })}>Sign out</button>
        </div>
      </div>
    );
  }
  return <Outlet />;
}
