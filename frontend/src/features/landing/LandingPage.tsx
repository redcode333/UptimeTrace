import { useEffect, useRef } from "react";
import "./landing.css";
import { LANDING_MARKUP } from "./landingMarkup";
import { startLanding } from "./landingRuntime";

/**
 * The public front door: the Claude Design hero, rendered as static markup and animated by the runtime in ./landingRuntime.
 * Calls to action go to the existing sign-in and onboarding routes; RequireAuth handles the rest.
 * The playground is an in-browser demo and never contacts a server.
 */
export function LandingPage() {
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const previousTitle = document.title;
    document.title = "UptimeTrace: test your alerts before the outage does";
    const root = rootRef.current;
    const stop = root ? startLanding(root) : undefined;
    return () => {
      stop?.();
      document.title = previousTitle;
    };
  }, []);

  return <div className="landing" ref={rootRef} dangerouslySetInnerHTML={{ __html: LANDING_MARKUP }} />;
}
