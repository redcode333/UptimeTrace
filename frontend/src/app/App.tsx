import { Route, Routes } from "react-router-dom";
import { SignInPage, SignUpPage } from "../auth/AuthPages";
import { RequireAuth } from "../auth/RequireAuth";
import { AppShell } from "../components/shell/AppShell";
import { flatNav } from "../components/shell/nav";
import { MetricsExplorerPage } from "../features/explorer/MetricsExplorerPage";
import { HostDetailPage } from "../features/hosts/HostDetailPage";
import { HostsPage } from "../features/hosts/HostsPage";
import { OverviewPage } from "../features/overview/OverviewPage";
import { UnavailablePage } from "../features/StubPages";

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/sign-in/*" element={<SignInPage />} />
      <Route path="/sign-up/*" element={<SignUpPage />} />
      <Route element={<RequireAuth />}>
        <Route element={<AppShell />}>
          <Route index element={<OverviewPage />} />
          <Route path="hosts" element={<HostsPage />} />
          <Route path="hosts/:hostId" element={<HostDetailPage />} />
          <Route path="metrics" element={<MetricsExplorerPage />} />
          {flatNav().filter((i) => i.unavailable).map((i) => (
            <Route key={i.path} path={i.path.slice(1)} element={<UnavailablePage />} />
          ))}
          <Route path="*" element={<UnavailablePage />} />
        </Route>
      </Route>
    </Routes>
  );
}

export function MissingConfig() {
  return (
    <div className="centered">
      <div className="card" role="alert">
        <span className="flag crit">NOT CONFIGURED</span>
        <h1>Clerk is not configured</h1>
        <p>
          Set <span className="mono">VITE_CLERK_PUBLISHABLE_KEY</span> in <span className="mono">frontend/.env.local</span> (see{" "}
          <span className="mono">frontend/.env.example</span>) and restart the dev server. The publishable key is public; never put a
          secret key in a <span className="mono">VITE_</span> variable.
        </p>
      </div>
    </div>
  );
}
