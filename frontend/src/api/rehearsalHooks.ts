import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useApi } from "./context";
import type { Rehearsal, StartRehearsalIn } from "./rehearsalTypes";

/** Scenario catalogue: changes only on deployment, so it is cached for a while. */
export function useRehearsalCatalogue() {
  const api = useApi();
  return useQuery({ queryKey: ["rehearsal", "catalogue"], queryFn: ({ signal }) => api.rehearsalCatalogue(signal), staleTime: 300_000 });
}

/** Registered hosts with their eligibility. Refreshed so a newly registered synthetic host appears without a reload. */
export function useRehearsalTargets() {
  const api = useApi();
  return useQuery({ queryKey: ["rehearsal", "targets"], queryFn: ({ signal }) => api.rehearsalTargets(signal), refetchInterval: 15_000 });
}

export function useRehearsals() {
  const api = useApi();
  return useQuery({ queryKey: ["rehearsal", "list"], queryFn: ({ signal }) => api.rehearsals(signal), refetchInterval: 15_000 });
}

/** Rehearsals whose verdict cites this alert. Empty for an alert no rehearsal raised. */
export function useRehearsalsForAlert(alertId: string | undefined) {
  const api = useApi();
  return useQuery({
    queryKey: ["rehearsal", "byAlert", alertId],
    enabled: !!alertId,
    queryFn: ({ signal }) => api.rehearsals(signal, alertId),
    retry: false,
  });
}

/** One rehearsal. Polled every 2 s while it is RUNNING, so progress and the final verdict appear as the executor writes them. */
export function useRehearsal(id: string | undefined) {
  const api = useApi();
  return useQuery({
    queryKey: ["rehearsal", "item", id],
    enabled: !!id,
    queryFn: ({ signal }) => api.rehearsal(id!, signal),
    refetchInterval: (query) => (query.state.data?.execution === "RUNNING" ? 2_000 : false),
    retry: false,
  });
}

export function useRehearsalMutations() {
  const api = useApi();
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: ["rehearsal"] });
  return {
    start: useMutation({ mutationFn: (body: StartRehearsalIn) => api.startRehearsal(body), onSuccess: refresh }),
    stop: useMutation({ mutationFn: (id: string) => api.stopRehearsal(id), onSuccess: (r: Rehearsal) => {
      qc.setQueryData(["rehearsal", "item", r.id], (prev: Rehearsal | undefined) => (prev ? { ...prev, ...r, steps: prev.steps } : r));
      void refresh();
    } }),
  };
}
