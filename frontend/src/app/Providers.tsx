import { ClerkProvider, useAuth, useClerk } from "@clerk/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useMemo, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { ApiProvider } from "../api/context";
import { createApiClient } from "../api/client";
import { clerkAppearance } from "../auth/clerkAppearance";
import { apiBaseUrl } from "./config";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 10_000, refetchOnWindowFocus: true, retry: 1 } },
});

/** Bridges Clerk to the API client: a fresh session token per request, one forced refresh on 401, then sign out. */
function ApiBridge({ children }: { children: ReactNode }) {
  const { getToken } = useAuth();
  const clerk = useClerk();
  const client = useMemo(
    () =>
      createApiClient({
        baseUrl: apiBaseUrl,
        getToken: (opts) => getToken(opts?.skipCache ? { skipCache: true } : undefined),
        onUnauthorized: () => {
          queryClient.clear();
          void clerk.signOut({ redirectUrl: "/sign-in" });
        },
      }),
    [getToken, clerk],
  );
  return <ApiProvider client={client}>{children}</ApiProvider>;
}

export function Providers({ publishableKey, children }: { publishableKey: string; children: ReactNode }) {
  const navigate = useNavigate();
  return (
    <ClerkProvider
      publishableKey={publishableKey}
      appearance={clerkAppearance}
      signInUrl="/sign-in"
      signUpUrl="/sign-up"
      signInFallbackRedirectUrl="/"
      signUpFallbackRedirectUrl="/"
      routerPush={(to) => navigate(to)}
      routerReplace={(to) => navigate(to, { replace: true })}
    >
      <QueryClientProvider client={queryClient}>
        <ApiBridge>{children}</ApiBridge>
      </QueryClientProvider>
    </ClerkProvider>
  );
}
