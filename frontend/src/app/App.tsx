import { Route, Routes } from "react-router-dom";
import { SignInPage, SignUpPage } from "../auth/AuthPages";
import { RequireAuth } from "../auth/RequireAuth";
import { AppShell } from "../components/shell/AppShell";
import { flatNav } from "../components/shell/nav";
import { MetricsExplorerPage } from "../features/explorer/MetricsExplorerPage";
import { AgentsPage } from "../features/agents/AgentsPage";
import { AlertPoliciesPage } from "../features/settings/AlertPoliciesPage";
import { IntegrationsPage } from "../features/settings/IntegrationsPage";
import { OnboardingPage } from "../features/onboarding/OnboardingPage";
import { InfrastructurePage } from "../features/infrastructure/InfrastructurePage";
import { AlertsPage } from "../features/alerts/AlertsPage";
import { InvestigationPage } from "../features/investigation/InvestigationPage";
import { InvestigationsListPage } from "../features/investigation/InvestigationsListPage";
import { StatesPage } from "../features/states/StatesPage";
import { SystemPage } from "../features/system/SystemPage";
import { RehearsalPage } from "../features/rehearsal/RehearsalPage";
import { RehearsalDetailPage } from "../features/rehearsal/RehearsalDetailPage";
import { HostDetailPage } from "../features/hosts/HostDetailPage";
import { HostsPage } from "../features/hosts/HostsPage";
import { OverviewPage } from "../features/overview/OverviewPage";
import { UnavailablePage } from "../features/StubPages";
import { LandingPage } from "../features/landing/LandingPage";
import { SignalGraphPage } from "../features/signals/SignalGraphPage";

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/sign-in/*" element={<SignInPage />} />
      <Route path="/sign-up/*" element={<SignUpPage />} />
      <Route path="/welcome" element={<LandingPage />} />
      <Route element={<RequireAuth />}>
        <Route element={<AppShell />}>
          <Route index element={<OverviewPage />} />
          <Route path="hosts" element={<HostsPage />} />
          <Route path="hosts/:hostId" element={<HostDetailPage />} />
          <Route path="metrics" element={<MetricsExplorerPage />} />
          <Route path="signal-graph" element={<SignalGraphPage />} />
          <Route path="settings/agents" element={<AgentsPage />} />
          <Route path="settings/alerts" element={<AlertPoliciesPage />} />
          <Route path="settings/integrations" element={<IntegrationsPage />} />
          <Route path="onboarding" element={<OnboardingPage />} />
          <Route path="infrastructure" element={<InfrastructurePage />} />
          <Route path="alerts" element={<AlertsPage />} />
          <Route path="investigations" element={<InvestigationsListPage />} />
          <Route path="states" element={<StatesPage />} />
          <Route path="system" element={<SystemPage />} />
          <Route path="alerts/:alertId" element={<InvestigationPage />} />
          <Route path="rehearsal" element={<RehearsalPage />} />
          <Route path="rehearsal/:rehearsalId" element={<RehearsalDetailPage />} />
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
