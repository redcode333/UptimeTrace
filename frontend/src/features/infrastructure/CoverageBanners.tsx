export interface Coverage { loaded: number; total: number; complete: boolean }

/** Partial or failed loads, stated plainly. Shared by the topology and 3D views so the wording cannot drift. */
export function CoverageBanners({ hostCoverage, alertCoverage }: { hostCoverage: Coverage | null; alertCoverage: Coverage | null }) {
  return (
    <>
      {hostCoverage && !hostCoverage.complete && (
        <p className="banner notice" role="status">
          Monitoring data is loaded for {hostCoverage.loaded} of {hostCoverage.total} hosts. Hosts without loaded data show "no data loaded", not a health state.
        </p>
      )}
      {hostCoverage === null && (
        <p className="banner warn" role="alert">Monitoring data could not be loaded. The structure below is still current, but no host health is shown.</p>
      )}
      {alertCoverage && !alertCoverage.complete && (
        <p className="banner notice" role="status">
          Showing {alertCoverage.loaded} of {alertCoverage.total} active alerts. Alert indicators may be incomplete.
        </p>
      )}
      {alertCoverage === null && (
        <p className="banner warn" role="alert">Active alerts could not be loaded. Alert indicators are not shown.</p>
      )}
    </>
  );
}
