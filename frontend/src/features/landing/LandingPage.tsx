import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import "./landing.css";
import { LANDING_MARKUP } from "./landingMarkup";
import { startLanding } from "./landingRuntime";
import { ProductShowcase } from "./showcase/ProductShowcase";

/**
 * The public front door: the Claude Design hero, rendered as static markup and animated by the runtime in ./landingRuntime.
 * Below the hero, the product showcase mounts the real console screens into the markup's #showcase-mount.
 * Calls to action go to the existing sign-in and onboarding routes; RequireAuth handles the rest.
 * The playground is an in-browser demo and never contacts a server.
 */
export function LandingPage() {
  const rootRef = useRef<HTMLDivElement>(null);
  const [showcaseMount, setShowcaseMount] = useState<HTMLElement | null>(null);

  useEffect(() => {
    const previousTitle = document.title;
    document.title = "UptimeTrace: test your alerts before the outage does";
    const root = rootRef.current;
    const stop = root ? startLanding(root) : undefined;
    setShowcaseMount(root?.querySelector<HTMLElement>("#showcase-mount") ?? null);
    return () => {
      stop?.();
      document.title = previousTitle;
    };
  }, []);

  return (
    <div className="landing" ref={rootRef}>
      <div dangerouslySetInnerHTML={{ __html: LANDING_MARKUP }} />
      {showcaseMount && createPortal(<ProductShowcase />, showcaseMount)}
    </div>
  );
}
