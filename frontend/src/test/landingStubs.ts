/** Browser APIs that jsdom lacks and the landing page's motion code needs. Installed once per test file. */
export function installLandingStubs(): void {
  if (!window.matchMedia) {
    window.matchMedia = ((query: string) => ({
      matches: false, media: query, onchange: null,
      addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
  }
  class Noop {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  Object.assign(window, { IntersectionObserver: Noop, ResizeObserver: Noop });
  // a 2D context that accepts every drawing call and does nothing (the hero's sprite textures are drawn at start-up)
  const fake2d: Record<PropertyKey, unknown> = new Proxy({} as Record<PropertyKey, unknown>, {
    get: (target, prop) => {
      if (prop in target) return target[prop];
      if (prop === "createRadialGradient" || prop === "createLinearGradient") return () => ({ addColorStop() {} });
      return () => undefined;
    },
    set: (target, prop, value) => {
      target[prop] = value;
      return true;
    },
  });
  HTMLCanvasElement.prototype.getContext = (() => fake2d) as unknown as HTMLCanvasElement["getContext"];
}
