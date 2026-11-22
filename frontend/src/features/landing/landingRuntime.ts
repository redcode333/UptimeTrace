// @ts-nocheck
// Runtime for the landing page, ported from the Claude Design hero artifact (scripts on lines 683-1441 of the saved source).
// The static markup is rendered by LandingPage; these functions attach the motion, the hero scene and the playground to it.
// Every timer, frame and renderer started here is released by the function returned to React.
import * as THREE from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

export function startLanding(root: HTMLElement): () => void {
  let alive = true;
  const renderers: any[] = [];
  const timers: any[] = [];
  const raf = (f: FrameRequestCallback) => (alive ? requestAnimationFrame(f) : 0);
  const every = (f: () => void, ms: number) => { const id = setInterval(f, ms); timers.push(id); return id; };
  // clear anything a previous run of this runtime left in the static markup
  for (const sel of ["#parts", "#labels", "#wires"]) { const el = root.querySelector(sel); if (el) el.innerHTML = ""; }
  const savedTheme = (() => { try { return localStorage.getItem("ut-landing-theme"); } catch { return null; } })();
  if (savedTheme) root.dataset.theme = savedTheme;

/* Where the CTAs go: set APP_URL to your app's origin and every [data-app] link points at APP_URL + its path.
   Left empty, the links scroll to the playground. */

const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches

root.querySelector("#theme").addEventListener("click", () => {
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches
  root.dataset.theme = dark ? "light" : "dark"
  try { localStorage.setItem("ut-landing-theme", root.dataset.theme) } catch {}
  dispatchEvent(new Event("themechange"))
})

/* MCP line: the agent names roll past one after another. The track is the list plus a repeat of its first name, so the
   loop is seamless: at the repeat it snaps back to the start. Reduced motion shows the first name only. */
{
  const roller = root.querySelector<HTMLElement>("#roller");
  const track = roller?.querySelector<HTMLElement>(".rtrack");
  if (roller && track) {
    const items = [...track.children] as HTMLElement[];
    const n = items.length - 1;
    let k = 0;
    const fit = () => { roller.style.width = items[k].getBoundingClientRect().width + "px"; };
    fit();
    document.fonts?.ready.then(fit);
    addEventListener("resize", fit);
    if (reduce) items.forEach((el, i) => { if (i) el.style.display = "none"; });
    else every(() => {
      if (document.hidden) return;
      k++;
      track.style.transition = "";
      track.style.transform = `translateY(${-34 * k}px)`;
      fit();
      if (k === n) setTimeout(() => { track.style.transition = "none"; k = 0; track.style.transform = "translateY(0)"; fit(); }, 650);
    }, 2200);
  }
}

/* Logo: ring and trace draw in, the dot pops and pings; replays on hover, then idles. */
root.querySelectorAll("[data-logo]").forEach((el, i) => {
  if (reduce) return
  let until = 0, t
  const play = () => {
    if (Date.now() < until) return
    until = Date.now() + 1800; clearTimeout(t)
    el.classList.remove("on", "idle"); void el.getBoundingClientRect(); el.classList.add("on")
    t = setTimeout(() => { el.classList.remove("on"); el.classList.add("idle") }, 2000)
  }
  if (i === 0) play(); else el.classList.add("idle")
  el.addEventListener("mouseenter", play); el.addEventListener("focus", play)
})



/* Glass nav: the lens slides to the hovered or current section; icons play a small move on click. */
const nav = root.querySelector("#pillnav"), lens = nav.querySelector(".lens"), links = [...nav.querySelectorAll("a")]
let current = -1
const moveTo = i => { if (i < 0) { lens.style.opacity = 0; return } lens.style.opacity = 1; const a = links[i]; lens.style.width = a.offsetWidth + "px"; lens.style.transform = `translateX(${a.offsetLeft}px)` }
const setCurrent = i => { current = i; links.forEach((a, k) => a.setAttribute("aria-current", k === i ? "true" : "false")); moveTo(i) }
links.forEach((a, i) => {
  a.addEventListener("mouseenter", () => moveTo(i))
  a.addEventListener("click", () => { const ico = a.querySelector(".ico"); ico.className = "ico"; void ico.offsetWidth; ico.classList.add("fx-" + a.dataset.fx) })
})
nav.addEventListener("mouseleave", () => moveTo(current))
const spy = new IntersectionObserver(es => es.forEach(e => {
  if (!e.isIntersecting) return
  setCurrent(links.findIndex(a => a.hash === "#" + e.target.id))
}), { rootMargin: "-45% 0px -50% 0px" })
;["hero", "product", "drills", "playground", "integrations", "faq"].forEach(id => spy.observe(document.getElementById(id)))
addEventListener("resize", () => moveTo(current))

/* Loop cards lean toward the pointer. */
if (!reduce) root.querySelectorAll("[data-tilt]").forEach(c => {
  c.addEventListener("pointermove", e => { const r = c.getBoundingClientRect(), x = (e.clientX - r.left) / r.width - .5, y = (e.clientY - r.top) / r.height - .5; c.style.transform = `perspective(900px) rotateY(${x * 8}deg) rotateX(${-y * 8}deg)` })
  c.addEventListener("pointerleave", () => { c.style.transform = "" })
})
const chart = root.querySelector("#chart")
new IntersectionObserver((es, o) => es.forEach(e => { if (e.isIntersecting && !reduce) { chart.classList.add("play"); o.disconnect() } }), { threshold: .4 }).observe(chart)

/* Hero headline: the last phrase rotates, letters leaving and arriving in sequence. */
;(() => {
  const rot = root.querySelector("#rot"); if (!rot || reduce) return
  const phrases = ["the 3 a.m. outage", "Friday's deploy", "your biggest customer", "the next incident"]
  const letters = t => [...t].map((c, i) => `<i style="--i:${i}">${c}</i>`).join("")
  const measure = t => { const m = document.createElement("span"); m.className = "rw"; m.style.cssText = "position:absolute;visibility:hidden"; m.textContent = t; rot.append(m); const w = m.getBoundingClientRect().width; m.remove(); return w }
  let k = 0, w = rot.querySelector(".rw")
  // Reserve the tallest headline, so a longer phrase never moves the lines below it (the MCP line and the model fit depend on them).
  const h1 = root.querySelector<HTMLElement>(".hero h1")
  const reserve = () => {
    if (!h1) return
    h1.style.minHeight = ""
    let tallest = 0
    for (const p of phrases) { w.innerHTML = letters(p); tallest = Math.max(tallest, h1.offsetHeight) }
    h1.style.minHeight = tallest + "px"
    w.innerHTML = letters(phrases[k])
  }
  reserve(); if (document.fonts) document.fonts.ready.then(reserve)
  w.innerHTML = letters(phrases[0]); rot.style.width = measure(phrases[0]) + "px"
  addEventListener("resize", () => { reserve(); rot.style.width = measure(phrases[k]) + "px" })
  every(() => {
    if (document.hidden) return
    w.className = "rw out"
    setTimeout(() => {
      k = (k + 1) % phrases.length
      rot.style.width = measure(phrases[k]) + "px"
      w.className = "rw"; w.innerHTML = letters(phrases[k]); void w.offsetWidth; w.className = "rw in"
    }, 420)
  }, 3200)
})()

/* MCP clients: each client appears in turn with its own wire, holds, fades out, and the sequence starts again. Reduced motion shows them all. */
;(() => {
  const chips = [...root.querySelectorAll(".h-agents .chip[data-seq]")]
  if (!chips.length) return
  const setSeq = (i, on) => root.querySelectorAll(`[data-seq="${i}"]`).forEach(el => el.classList.toggle("on", on))
  if (reduce) { chips.forEach((_, i) => setSeq(i, true)); return }
  const STEP = 900, LEAD = 400, HOLD = 2600, GAP = 900
  const run = () => {
    chips.forEach((_, i) => timers.push(setTimeout(() => setSeq(i, true), LEAD + i * STEP)))
    const off = LEAD + chips.length * STEP + HOLD
    timers.push(setTimeout(() => chips.forEach((_, i) => setSeq(i, false)), off))
    timers.push(setTimeout(run, off + GAP))
  }
  run()
})()

/* Scroll reveals: content below the fold rises in as it arrives; siblings stagger. */
;(() => {
  if (reduce) return
  const els = [...root.querySelectorAll(".rv")]
  els.forEach(el => { const sib = [...el.parentElement.children].filter(c => c.classList.contains("rv")); el.style.setProperty("--d", sib.indexOf(el)) })
  root.classList.add("rv-on")
  const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target) } }), { rootMargin: "0px 0px -8% 0px" })
  els.forEach(el => { if (el.getBoundingClientRect().top < innerHeight * .92) el.classList.add("in"); else io.observe(el) })
})()

/* Integration wires: measured from the real chip positions, drawn in when the hub scrolls into view,
   with packets running along them (requests into UptimeTrace, alerts out of it). */
;(() => {
  const hub = root.querySelector("#hub"), svg = root.querySelector("#wires"), NS = "http://www.w3.org/2000/svg"
  const box = el => { let x = 0, y = 0, n = el; while (n && n !== hub) { x += n.offsetLeft; y += n.offsetTop; n = n.offsetParent } return { x, y, w: el.offsetWidth, h: el.offsetHeight } }
  const draw = () => {
    svg.setAttribute("viewBox", `0 0 ${hub.offsetWidth} ${hub.offsetHeight}`); svg.innerHTML = ""
    let k = 0
    const link = (from, to, out) => {
      const a = box(from), b = box(to), dx = (b.x + b.w / 2) - (a.x + a.w / 2), dy = (b.y + b.h / 2) - (a.y + a.h / 2)
      const horiz = Math.abs(dx) > Math.abs(dy) * 1.2
      const p1 = horiz ? [dx > 0 ? a.x + a.w : a.x, a.y + a.h / 2] : [a.x + a.w / 2, dy > 0 ? a.y + a.h : a.y]
      const p2 = horiz ? [dx > 0 ? b.x : b.x + b.w, b.y + b.h / 2] : [b.x + b.w / 2, dy > 0 ? b.y : b.y + b.h]
      const mx = (p1[0] + p2[0]) / 2, my = (p1[1] + p2[1]) / 2
      const d = horiz ? `M${p1} C${mx},${p1[1]} ${mx},${p2[1]} ${p2}` : `M${p1} C${p1[0]},${my} ${p2[0]},${my} ${p2}`
      const id = "wire" + k, path = document.createElementNS(NS, "path")
      path.setAttribute("d", d); path.setAttribute("id", id); path.setAttribute("pathLength", "1"); path.style.transitionDelay = k * 70 + "ms"; svg.append(path)
      const seq = from.dataset.seq
      if (seq !== undefined) { path.dataset.seq = seq; if (from.classList.contains("on")) path.classList.add("on") }
      if (!reduce) {
        const c = document.createElementNS(NS, "circle"); c.setAttribute("r", "3"); if (out) c.classList.add("out"); if (seq !== undefined) { c.dataset.seq = seq; if (from.classList.contains("on")) c.classList.add("on") }
        const am = document.createElementNS(NS, "animateMotion"); am.setAttribute("dur", (2.2 + (k % 4) * .45) + "s"); am.setAttribute("repeatCount", "indefinite"); am.setAttribute("begin", ((k * .41) % 2.4).toFixed(2) + "s")
        const mp = document.createElementNS(NS, "mpath"); mp.setAttribute("href", "#" + id); am.append(mp); c.append(am); svg.append(c)
      }
      k++
    }
    hub.querySelectorAll("[data-to]").forEach(el => link(el, document.getElementById(el.dataset.to), false))
    hub.querySelectorAll("[data-from]").forEach(el => link(document.getElementById(el.dataset.from), el, true))
  }
  draw(); new ResizeObserver(draw).observe(hub); if (document.fonts) document.fonts.ready.then(draw)
  if (reduce) hub.classList.add("in")
  else new IntersectionObserver((es, o) => es.forEach(e => { if (e.isIntersecting) { hub.classList.add("in"); o.disconnect() } }), { threshold: .2 }).observe(hub)
})()

/* Footer wordmark rises letter by letter as the footer scrolls up. */
;(() => {
  if (reduce) return
  const g = root.querySelector("#giant"), f = root.querySelector("#foot")
  const upd = () => { const r = f.getBoundingClientRect(), p = Math.min(1, Math.max(0, (innerHeight - r.top) / (r.height * .85))); g.style.setProperty("--p", p.toFixed(3)) }
  addEventListener("scroll", upd, { passive: true }); addEventListener("resize", upd); upd()
})()

/* Background dot field: dots ease away from the cursor and spring back. */
;(() => {
  const cv = root.querySelector("#field"), g = cv.getContext("2d"), GAP = 26, R = 130, PUSH = 18
  if (!g) return
  let W, H, dots = [], mx = -1e4, my = -1e4, color = "", running = false
  const size = () => {
    const dpr = Math.min(devicePixelRatio, 2); W = innerWidth; H = innerHeight
    cv.width = W * dpr; cv.height = H * dpr; g.setTransform(dpr, 0, 0, dpr, 0, 0)
    dots = []; for (let y = GAP / 2; y < H + GAP; y += GAP) for (let x = GAP / 2; x < W + GAP; x += GAP) dots.push({ x, y, ox: 0, oy: 0 })
    color = getComputedStyle(root).getPropertyValue("--grid").trim(); draw()
  }
  const draw = () => {
    g.clearRect(0, 0, W, H); g.fillStyle = color
    let moving = false
    for (const d of dots) {
      let tx = 0, ty = 0
      const dx = d.x - mx, dy = d.y - my, dist = Math.hypot(dx, dy)
      if (dist < R && !reduce) { const f = (1 - dist / R) ** 2 * PUSH; tx = dx / (dist || 1) * f; ty = dy / (dist || 1) * f }
      d.ox += (tx - d.ox) * .16; d.oy += (ty - d.oy) * .16
      if (Math.abs(tx - d.ox) > .05 || Math.abs(ty - d.oy) > .05) moving = true
      const s = 1 + Math.min(1.4, Math.hypot(d.ox, d.oy) / 10)
      g.beginPath(); g.arc(d.x + d.ox, d.y + d.oy, s, 0, 6.283); g.fill()
    }
    return moving
  }
  const tick = () => { running = draw(); if (running) raf(tick) }
  const kick = () => { if (!running) { running = true; raf(tick) } }
  addEventListener("pointermove", e => { mx = e.clientX; my = e.clientY; kick() }, { passive: true })
  document.addEventListener("pointerleave", () => { mx = my = -1e4; kick() })
  addEventListener("resize", size); addEventListener("themechange", size)
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", size)
  size()
})()

const glowTex = (() => {
  const c = document.createElement("canvas"); c.width = c.height = 64; const g = c.getContext("2d")
  const gr = g.createRadialGradient(32, 32, 0, 32, 32, 32); gr.addColorStop(0, "rgba(255,255,255,1)"); gr.addColorStop(.25, "rgba(255,255,255,.5)"); gr.addColorStop(1, "rgba(255,255,255,0)")
  g.fillStyle = gr; g.fillRect(0, 0, 64, 64); return new THREE.CanvasTexture(c)
})()
const ventTex = (() => {
  const c = document.createElement("canvas"); c.width = 256; c.height = 32; const g = c.getContext("2d")
  g.fillStyle = "#1d2130"; g.fillRect(0, 0, 256, 32); g.fillStyle = "#0e1019"; for (let x = 10; x < 170; x += 7) g.fillRect(x, 8, 3, 16)
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t
})()
const makeRenderer = canvas => {
  try {
    const r = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: !!window.__capture })
    // Without a live WebGL context three.js still returns a renderer object, but it draws nothing. Treat that as "no WebGL"
    // so the callers keep the static poster and do not mark the hero as live.
    const gl = r.getContext()
    if (!gl || gl.isContextLost()) { r.dispose(); return null }
    r.setPixelRatio(Math.min(devicePixelRatio, 2)); r.outputColorSpace = THREE.SRGBColorSpace; r.toneMapping = THREE.ACESFilmicToneMapping; renderers.push(r); return r
  } catch { return null }
}

/* ======================= HERO: three servers floating above a sea of real cloud photographs ======================= */
;(() => {
  const host = root.querySelector("#hero"), canvas = host.querySelector("canvas")
  const renderer = makeRenderer(canvas); if (!renderer) return
  renderer.toneMappingExposure = 1.05
  const scene = new THREE.Scene()
  const pm = new THREE.PMREMGenerator(renderer)
  scene.environment = pm.fromScene(new RoomEnvironment(), 0.04).texture; scene.environmentIntensity = .85
  const camera = new THREE.PerspectiveCamera(30, 1, .1, 200)
  const look = new THREE.Vector3(0, -.2, 0)
  const key = new THREE.DirectionalLight("#ffffff", 2.2); key.position.set(-4, 6, 5); scene.add(key)
  const rim = new THREE.DirectionalLight("#bcd4ff", 1.6); rim.position.set(5, 2, -4); scene.add(rim)
  const css = n => getComputedStyle(root).getPropertyValue(n).trim()
  const col = { up: new THREE.Color(), warn: new THREE.Color(), crit: new THREE.Color(), ink: new THREE.Color(), accent: new THREE.Color(), cloud: new THREE.Color() }
  const readColors = () => { for (const k in col) col[k].set(css("--" + k)) }
  readColors()
  let seed = 11; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647

  // --- clouds: CC0 photographic cloud texture, layered as soft sprites
  const cloudTex = new THREE.TextureLoader().load("/assets/landing/cloud.webp")
  cloudTex.colorSpace = THREE.SRGBColorSpace
  const puffs = []
  const bank = (cx, cy, cz, w, n, size, shade) => {
    for (let k = 0; k < n; k++) {
      const m = new THREE.SpriteMaterial({ map: cloudTex, transparent: true, depthWrite: false, opacity: .42 + rnd() * .42, rotation: rnd() * 6.283 })
      const sp = new THREE.Sprite(m), sc = size * (.6 + rnd() * .8)
      sp.scale.set(sc, sc * (.75 + rnd() * .3), 1)
      sp.position.set(cx + (rnd() - .5) * w, cy + (rnd() - .35) * size * .3, cz + (rnd() - .5) * 2.5)
      sp.userData = { base: sp.position.clone(), ox: 0, oy: 0, drift: .04 + rnd() * .07, shade: shade * (.95 + rnd() * .05), w: 46 }
      scene.add(sp); puffs.push(sp)
    }
  }
  for (let i = 0; i < 10; i++) bank(-23 + i * 5, -4.4 - rnd() * .5, -3 - rnd() * 7, 6.5, 6, 4.6, 1)   // the sea below
  for (let i = 0; i < 6; i++) bank(-16 + i * 6.5, -5.9, 3.5, 5, 4, 4.8, 1)                          // close puffs at the bottom edge
  bank(-12.5, 4.2, -16, 8, 6, 6.5, .97); bank(13, 5.4, -19, 9, 7, 7, .97); bank(17, 1.2, -12, 5, 5, 4.4, .93); bank(-18, .6, -13, 5, 4, 4, .93)
  const tint = () => puffs.forEach(p => p.material.color.copy(col.cloud).multiplyScalar(p.userData.shade))
  tint()

  // --- the server stack, floating
  const world = new THREE.Group(); scene.add(world)
  const vents = (() => {
    const c = document.createElement("canvas"); c.width = 512; c.height = 64; const g = c.getContext("2d")
    g.fillStyle = "#1a2230"; g.fillRect(0, 0, 512, 64); g.fillStyle = "#0c1119"; for (let x = 16; x < 330; x += 9) g.fillRect(x, 14, 4, 36)
    g.fillStyle = "#2a3445"; g.fillRect(352, 22, 70, 20)
    const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4; return t
  })()
  const bodyMat = new THREE.MeshPhysicalMaterial({ color: 0xe3e8ef, metalness: .7, roughness: .28, clearcoat: .6, clearcoatRoughness: .2 })
  const faceMat = new THREE.MeshStandardMaterial({ map: vents, roughness: .55, metalness: .3 })
  const bladeGeo = new RoundedBoxGeometry(2.7, .36, 1.8, 4, .06), faceGeo = new THREE.PlaneGeometry(2.42, .22)
  const blades = [], stack = new THREE.Group(); world.add(stack)
  for (let i = 0; i < 3; i++) {
    const b = new THREE.Group(); b.position.y = (i - 1) * .47
    b.add(new THREE.Mesh(bladeGeo, bodyMat))
    const face = new THREE.Mesh(faceGeo, faceMat); face.position.set(0, 0, .901); b.add(face)
    const ledMat = new THREE.MeshBasicMaterial({ color: col.up.clone() }), led = new THREE.Mesh(new THREE.SphereGeometry(.032, 16, 12), ledMat)
    led.position.set(1.0, 0, .92); b.add(led)
    const glow = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex, color: col.up.clone(), transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, opacity: .9 }))
    glow.scale.setScalar(.32); glow.position.copy(led.position); b.add(glow)
    for (let k = 0; k < 2; k++) { const s = new THREE.Mesh(new THREE.SphereGeometry(.022, 12, 8), new THREE.MeshBasicMaterial({ color: 0x3b4a60 })); s.position.set(.86 - k * .12, 0, .92); b.add(s) }
    stack.add(b); blades.push({ group: b, ledMat, glow })
  }
  const rings = [
    { r: 2.3, rot: [1.28, 0, .32], speed: .42, phase: .4 },
    { r: 2.7, rot: [1.02, .5, -.46], speed: -.3, phase: 2.2 },
    { r: 3.1, rot: [1.46, -.35, .12], speed: .22, phase: 4.1 },
  ].map(d => {
    const g = new THREE.Group(); g.rotation.set(...d.rot); world.add(g)
    const lineMat = new THREE.MeshBasicMaterial({ color: col.ink.clone(), transparent: true, opacity: .25 })
    g.add(new THREE.Mesh(new THREE.TorusGeometry(d.r, .0065, 6, 260), lineMat))
    const trailMat = new THREE.MeshBasicMaterial({ color: col.accent.clone(), transparent: true, opacity: .6 })
    const trail = new THREE.Mesh(new THREE.TorusGeometry(d.r, .014, 6, 64, .85), trailMat); g.add(trail)
    const beadMat = new THREE.MeshBasicMaterial({ color: col.accent.clone() }), bead = new THREE.Mesh(new THREE.SphereGeometry(.07, 20, 14), beadMat); g.add(bead)
    const glow = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex, color: col.accent.clone(), transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, opacity: .8 }))
    glow.scale.setScalar(.5); g.add(glow)
    return { ...d, g, lineMat, trail, trailMat, beadMat, bead, glow, a: d.phase }
  })
  const pulseMat = new THREE.MeshBasicMaterial({ color: col.crit.clone(), transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false })
  const pulse = new THREE.Mesh(new THREE.RingGeometry(1.75, 1.8, 128), pulseMat); pulse.rotation.x = -Math.PI / 2; world.add(pulse)

  // ---------- inspect: click the stack and it opens into its parts, each with a health verdict ----------
  /* To use Meshy models instead of the built-in parts, put the GLB files next to the page and list them here,
     e.g. { cpu: "assets/models/cpu.glb", ram: "assets/models/ram.glb" }. Each one is scaled to fit its slot. */
  const PART_MODELS = {}
  const stackHit = new THREE.Mesh(new THREE.BoxGeometry(3, 1.7, 2.1), new THREE.MeshBasicMaterial({ visible: false })); stack.add(stackHit)
  const M = {
    metal: new THREE.MeshStandardMaterial({ color: "#c9d0da", metalness: .85, roughness: .26 }),
    alu: new THREE.MeshStandardMaterial({ color: "#aab4c2", metalness: .8, roughness: .35 }),
    dark: new THREE.MeshStandardMaterial({ color: "#262d39", metalness: .45, roughness: .45 }),
    pcb: new THREE.MeshStandardMaterial({ color: "#1d5a43", metalness: .1, roughness: .55 }),
    pcbDark: new THREE.MeshStandardMaterial({ color: "#1a2030", metalness: .15, roughness: .5 }),
    chip: new THREE.MeshStandardMaterial({ color: "#13161c", metalness: .3, roughness: .32 }),
    gold: new THREE.MeshStandardMaterial({ color: "#d9a845", metalness: 1, roughness: .28 }),
    copper: new THREE.MeshStandardMaterial({ color: "#c47b4b", metalness: 1, roughness: .32 }),
    plastic: new THREE.MeshStandardMaterial({ color: "#2b313c", metalness: .1, roughness: .7 }),
    label: new THREE.MeshStandardMaterial({ color: "#e9eef5", roughness: .6 }),
  }
  const B = (g, w, h, d, m, x = 0, y = 0, z = 0, rx = 0, ry = 0, rz = 0) => { const o = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), m); o.position.set(x, y, z); o.rotation.set(rx, ry, rz); g.add(o); return o }
  const R = (g, w, h, d, r, m, x = 0, y = 0, z = 0) => { const o = new THREE.Mesh(new RoundedBoxGeometry(w, h, d, 3, r), m); o.position.set(x, y, z); g.add(o); return o }
  const blinkers = []
  const build = {
    cpu() { // processor on its substrate, with a copper-based cooler on top
      const g = new THREE.Group()
      B(g, 1.0, .06, 1.0, M.pcb)
      for (let i = 0; i < 4; i++) { const a = i * Math.PI / 2; const s = B(g, .86, .012, .05, M.gold, Math.sin(a) * .46, .036, Math.cos(a) * .46); s.rotation.y = a }
      R(g, .7, .07, .7, .02, M.metal, 0, .07, 0)
      R(g, .78, .07, .78, .02, M.copper, 0, .14, 0)
      for (let i = 0; i < 11; i++) B(g, .78, .34, .022, M.alu, 0, .35, -.36 + i * .072)
      g.rotation.set(.55, .5, 0); return g
    },
    ram() { // one memory module
      const g = new THREE.Group()
      B(g, 1.7, .38, .035, M.pcb)
      for (let i = 0; i < 8; i++) B(g, .16, .2, .03, M.chip, -.68 + i * .195, .03, .03)
      B(g, 1.6, .06, .04, M.gold, 0, -.17, 0)
      B(g, .07, .07, .05, M.pcb, .2, -.2, 0)
      g.rotation.set(.25, -.35, .12); return g
    },
    ssd() { // an NVMe drive
      const g = new THREE.Group()
      B(g, 1.45, .035, .38, M.pcbDark)
      B(g, .3, .045, .3, M.chip, -.36, .04, 0); B(g, .3, .045, .3, M.chip, .02, .04, 0); B(g, .2, .045, .2, M.chip, .38, .04, 0)
      B(g, .58, .006, .34, M.label, -.17, .066, 0)
      for (let i = 0; i < 9; i++) B(g, .05, .012, .022, M.gold, .7, .02, -.15 + i * .037)
      g.rotation.set(.85, .4, 0); return g
    },
    fan() { // a chassis fan with spinning blades
      const g = new THREE.Group()
      const t = .09
      B(g, 1, t, .26, M.plastic, 0, .455, 0); B(g, 1, t, .26, M.plastic, 0, -.455, 0); B(g, t, 1, .26, M.plastic, .455, 0, 0); B(g, t, 1, .26, M.plastic, -.455, 0, 0)
      const ring = new THREE.Mesh(new THREE.TorusGeometry(.43, .03, 10, 48), M.plastic); g.add(ring)
      const rotor = new THREE.Group(); g.add(rotor)
      const hub = new THREE.Mesh(new THREE.CylinderGeometry(.13, .13, .2, 28), M.dark); hub.rotation.x = Math.PI / 2; rotor.add(hub)
      for (let i = 0; i < 7; i++) { const a = i / 7 * Math.PI * 2; const bl = B(rotor, .3, .13, .02, M.plastic, Math.cos(a) * .25, Math.sin(a) * .25, 0); bl.rotation.set(.45, 0, a) }
      g.userData.rotor = rotor
      g.rotation.set(.1, -.45, 0); return g
    },
    nic() { // a network card with two ports and link lights
      const g = new THREE.Group()
      B(g, 1.25, .55, .035, M.pcb)
      B(g, .05, .7, .16, M.metal, .64, .04, .05)
      for (let k = 0; k < 2; k++) {
        B(g, .2, .2, .22, M.dark, .5, .12 - k * .24, .12)
        const lm = new THREE.MeshBasicMaterial({ color: "#5fe0a0" }); const l = new THREE.Mesh(new THREE.SphereGeometry(.022, 10, 8), lm); l.position.set(.66, .17 - k * .24, .2); g.add(l); blinkers.push({ m: lm, p: k * 1.7 })
      }
      B(g, .26, .26, .04, M.chip, -.15, .04, .03)
      B(g, 1.0, .05, .04, M.gold, -.1, -.3, 0)
      g.rotation.set(.18, -.6, 0); return g
    },
    psu() { // a power supply with a grille and socket
      const g = new THREE.Group()
      R(g, 1.1, .55, .78, .03, M.dark)
      const gr = new THREE.Group(); gr.position.set(0, .28, 0); gr.rotation.x = -Math.PI / 2; g.add(gr)
      for (let r = .08; r < .3; r += .055) gr.add(new THREE.Mesh(new THREE.TorusGeometry(r, .008, 6, 48), M.alu))
      B(g, .22, .14, .02, M.plastic, -.32, 0, .4); B(g, .36, .16, .01, M.label, .2, 0, .395)
      g.rotation.set(.35, .45, 0); return g
    },
  }
  const PARTS = [
    { id: "cpu", name: "Processor", status: "up", word: "Healthy", why: "38% load, 61 °C. Well within limits.", at: [-3.7, 1.35, .5], n: [-1.75, 2.9, .5] },
    { id: "fan", name: "Chassis fan", status: "up", word: "Healthy", why: "2,400 rpm and steady.", at: [3.7, 1.35, .4], n: [1.75, 2.9, .4] },
    { id: "ram", name: "Memory", status: "warn", word: "Needs work", why: "86% used and climbing 4% an hour.", at: [-4, -.75, 1.1], n: [-2.1, .05, 1.1] },
    { id: "nic", name: "Network card", status: "up", word: "Healthy", why: "1.2 Gb/s, no errors in 24 h.", at: [4, -.75, 1.0], n: [2.1, .05, 1.0] },
    { id: "ssd", name: "NVMe disk", status: "crit", word: "Faulty", why: "12 reallocated sectors. Replace soon.", at: [-2.9, -2.35, 2.1], n: [-1.9, -2.95, 2.1] },
    { id: "psu", name: "Power supply", status: "warn", word: "Needs work", why: "Backup unit offline. Running on one.", at: [2.9, -2.35, 2.0], n: [1.5, -2.5, 2.0] },
  ]
  const partsEl = root.querySelector("#parts"), hintEl = root.querySelector("#inspect-hint"), backBtn = root.querySelector("#reassemble")
  const gltfLoader = Object.keys(PART_MODELS).length ? new GLTFLoader() : null
  const parts = PARTS.map(d => {
    const holder = new THREE.Group(); holder.scale.setScalar(.001); holder.visible = false; world.add(holder)
    let model = build[d.id](); holder.add(model)
    const aura = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex, color: col[d.status].clone(), transparent: true, opacity: .35, blending: THREE.AdditiveBlending, depthWrite: false }))
    aura.scale.setScalar(2.1); aura.position.z = -.4; holder.add(aura)
    const hit = new THREE.Mesh(new THREE.SphereGeometry(.75, 12, 8), new THREE.MeshBasicMaterial({ visible: false })); holder.add(hit)
    const el = document.createElement("button"); el.type = "button"; el.className = "part glass"; el.tabIndex = -1
    el.innerHTML = `<span class="pn">${d.name}</span><span class="st ${d.status}">${d.word}</span><span class="pw">${d.why}</span>`
    partsEl.append(el)
    const part = { ...d, holder, model, aura, hit, el, focus: 0 }
    hit.userData.part = part
    el.addEventListener("click", () => setFocus(part))
    if (gltfLoader && PART_MODELS[d.id]) gltfLoader.load(PART_MODELS[d.id], gl => {
      const m = gl.scene, box = new THREE.Box3().setFromObject(m), s = box.getSize(new THREE.Vector3()), c = box.getCenter(new THREE.Vector3())
      m.position.sub(c); const wrap = new THREE.Group(); wrap.add(m); wrap.scale.setScalar(1.25 / Math.max(s.x, s.y, s.z))
      holder.remove(part.model); holder.add(wrap); part.model = wrap
    })
    return part
  })
  let open = false, ex = 0, focused = null
  const setFocus = p => { focused = focused === p ? null : p; parts.forEach(q => q.el.classList.toggle("on", q === focused)) }
  const setOpen = v => {
    open = v; host.classList.toggle("inspecting", v); focused = null; parts.forEach(q => q.el.classList.remove("on"))
    parts.forEach(p => { p.el.tabIndex = v ? 0 : -1 }); backBtn.tabIndex = v ? 0 : -1
  }
  backBtn.addEventListener("click", () => setOpen(false))
  window.__inspect = setOpen
  addEventListener("keydown", e => { if (e.key === "Escape" && open) setOpen(false) })
  const ray = new THREE.Raycaster(), ptr = new THREE.Vector2()
  const pickAt = e => {
    const r = host.getBoundingClientRect(); ptr.set((e.clientX - r.left) / r.width * 2 - 1, -((e.clientY - r.top) / r.height * 2 - 1)); ray.setFromCamera(ptr, camera)
    const hit = ray.intersectObjects([stackHit, ...(open ? parts.map(p => p.hit) : [])])[0]
    return hit ? (hit.object === stackHit ? "stack" : hit.object.userData.part) : null
  }
  canvas.addEventListener("click", e => {
    const u = pickAt(e)
    if (u === "stack") setOpen(!open)
    else if (u) setFocus(u)
  })
  canvas.addEventListener("pointermove", e => {
    const u = pickAt(e); canvas.style.cursor = u ? "pointer" : ""
    const show = u === "stack" && !open
    hintEl.classList.toggle("show", show)
    if (show) { const r = host.getBoundingClientRect(); hintEl.style.transform = `translate(${e.clientX - r.left + 16}px, ${e.clientY - r.top + 18}px)` }
  })
  canvas.addEventListener("pointerleave", () => hintEl.classList.remove("show"))
  const pv = new THREE.Vector3(), bigOut = x => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2) }
  const updateParts = (dt, t0) => {
    ex += ((open ? 1 : 0) - ex) * Math.min(1, dt * (reduce ? 60 : 4.2))
    const narrow = host.clientWidth / host.clientHeight < .9
    blades.forEach((b, i) => { b.group.position.y = (i - 1) * (.47 + ex * .5) })
    rings.forEach(r => { r.lineMat.opacity = .25 * (1 - ex * .85); r.trailMat.opacity = .6 * (1 - ex * .85); r.beadMat.transparent = true; r.beadMat.opacity = 1 - ex * .85; r.glow.material.opacity = .8 * (1 - ex) })
    const e = bigOut(Math.min(1, ex))
    blinkers.forEach(b => { b.m.color.set(Math.sin(t0 * 7 + b.p) > 0 ? "#5fe0a0" : "#1d4d36") })
    parts.forEach((p, i) => {
      p.holder.visible = ex > .01
      const tg = narrow ? p.n : p.at
      p.holder.position.set(tg[0] * e, tg[1] * e, tg[2] * e)
      p.focus += ((focused === p ? 1 : 0) - p.focus) * .12
      p.holder.scale.setScalar(Math.max(.001, ex) * (1 + p.focus * .25))
      p.model.rotation.y += reduce ? 0 : dt * .25
      p.holder.position.y += Math.sin(t0 * 1.1 + i) * .06 * ex
      if (p.model.userData.rotor) p.model.userData.rotor.rotation.z -= dt * 9
      p.aura.material.color.copy(col[p.status])
      p.aura.material.opacity = (p.status === "crit" ? .35 + Math.sin(t0 * 5) * .2 : .3) * ex
      pv.set(0, .95, 0); p.holder.localToWorld(pv); pv.project(camera)
      const W = host.clientWidth, cw = p.el.offsetWidth, ch = p.el.offsetHeight
      const cx = Math.min(W - cw - 8, Math.max(8, (pv.x * .5 + .5) * W - cw / 2)), cy = (-pv.y * .5 + .5) * host.clientHeight - ch
      p.el.style.transform = `translate(${Math.round(cx)}px, ${Math.round(cy)}px)`
      p.el.style.opacity = ex > .6 ? (ex - .6) / .4 : 0
    })
    backBtn.style.opacity = ex > .5 ? 1 : 0
    return ex
  }

  const retheme = () => { readColors(); tint(); rings.forEach(r => r.lineMat.color.copy(col.ink)) }
  addEventListener("themechange", retheme); matchMedia("(prefers-color-scheme: dark)").addEventListener("change", retheme)

  const mouse = new THREE.Vector2(9, 9); let px = 0, py = 0
  host.addEventListener("pointermove", e => { const r = host.getBoundingClientRect(); mouse.set((e.clientX - r.left) / r.width * 2 - 1, -((e.clientY - r.top) / r.height * 2 - 1)) }, { passive: true })
  host.addEventListener("pointerleave", () => mouse.set(9, 9))
  const size = () => {
    const w = host.clientWidth, h = host.clientHeight; renderer.setSize(w, h, false); camera.aspect = w / h
    const narrow = w / h < .9; camera.fov = narrow ? 46 : 30; camera.updateProjectionMatrix()
    camera.userData.z = narrow ? 19 : 17; world.position.y = narrow ? -2.2 : -1.55
    camera.userData.baseX = 0
    // Keep the stack clear of the buttons on any screen height: make room if needed, then aim the camera
    // so the top of the stack lands a little below the call-to-action row.
    const cta = host.querySelector(".mcp-line"), hr = host.getBoundingClientRect()
    const ctaBottom = cta.getBoundingClientRect().bottom - hr.top
    // Fit the scene into the space under the MCP line: aim so the top ring clears the line,
    // and shrink the model on short screens so it stays on the first screen.
    const ringTop = Math.max(.78, ...rings.map(o => { o.g.updateMatrix(); const m = o.g.matrix.elements; return o.r * Math.hypot(m[1], m[5]) }))
    const want = ctaBottom + (narrow ? 46 : 24), floor = h - (narrow ? 24 : 16)
    const proj = (y, ly) => { camera.position.set(0, 1.6, camera.userData.z); camera.lookAt(0, ly, 0); camera.updateMatrixWorld(); return (-new THREE.Vector3(0, y, 0).project(camera).y * .5 + .5) * h }
    const aim = sc => { const top = world.position.y + (ringTop + .1) * sc; let a = -8, b = 8, fa = proj(top, a) - want
      for (let i = 0; i < 40; i++) { const m = (a + b) / 2, fm = proj(top, m) - want; if (Math.sign(fm) === Math.sign(fa)) { a = m; fa = fm } else b = m } return (a + b) / 2 }
    // Medium size by default: the stack is about a third of a desktop hero and about half of a phone width. Shrinks further only on short screens.
    let sc = narrow ? 1 : 1.25, ly = aim(sc)
    while (sc > .85 && proj(world.position.y - (ringTop * .8) * sc, ly) > floor) { sc -= .05; ly = aim(sc) }
    if (sc <= .85 && proj(world.position.y - (ringTop * .8) * sc, ly) > floor) { host.style.minHeight = Math.ceil(h + proj(world.position.y - ringTop * .8 * sc, ly) - floor) + "px"; raf(size); return }
    camera.userData.lookY = ly; camera.userData.scale = sc
  }
  size(); new ResizeObserver(size).observe(host); document.fonts?.ready.then(size)
  const copy = root.querySelector(".hero-copy"); if (copy) new ResizeObserver(size).observe(copy)
  let visible = true; new IntersectionObserver(es => { visible = es[0].isIntersecting }).observe(host)
  const v = new THREE.Vector3(), clock = new THREE.Clock(); let t0 = reduce ? 1.2 : 0
  const PERIOD = 12
  const frame = () => {
    const dt = Math.min(clock.getDelta(), .05); if (!reduce) t0 += dt
    const t = t0 % PERIOD
    const mx = mouse.x > 5 ? 0 : mouse.x, my = mouse.y > 5 ? 0 : mouse.y
    px += (mx - px) * .04; py += (my - py) * .04
    const k = ex
    world.position.x = (camera.userData.baseX || 0) * (1 - k)
    world.scale.setScalar((camera.userData.scale || 1) * (1 - k) + k)
    look.set(0, camera.userData.lookY + (world.position.y - camera.userData.lookY) * k, 0)
    camera.position.set(px * .8 * (1 - k * .6), 1.6 + py * .4 + (world.position.y + 1.4 - 1.6) * k, camera.userData.z - k * 1.5); camera.lookAt(look)
    world.rotation.y = (-.35 + Math.sin(t0 * .12) * .12 + px * .3) * (1 - k) + (-.1 + px * .12) * k
    world.rotation.x = -py * .06
    world.children[0].position.y = Math.sin(t0 * .7) * .08

    // drill on the middle server: amber, red, an alert pulse, then recovery
    let c = col.up
    if (t > 3 && t < 3.8) c = col.warn; else if (t >= 3.8 && t < 8) c = col.crit
    const mid = blades[1]; mid.ledMat.color.copy(c); mid.glow.material.color.copy(c)
    mid.glow.material.opacity = t >= 3.8 && t < 8 ? (Math.sin(t0 * 9) > 0 ? 1 : .35) : .9
    blades.forEach((b, i) => { if (i !== 1) { b.ledMat.color.copy(col.up); b.glow.material.color.copy(col.up); b.glow.material.opacity = .65 + .3 * Math.sin(t0 * 2 + i) } })
    const p = t - 4.6
    if (p > 0 && p < 1.6 && !reduce) { const k = p / 1.6; pulse.scale.setScalar(1 + k * 1.3); pulseMat.opacity = .7 * (1 - k); pulseMat.color.copy(col.crit) } else pulseMat.opacity = 0
    rings.forEach((r, i) => {
      const alarm = i === 0 && t >= 3.8 && t < 8
      r.a += r.speed * dt * (alarm ? 2.4 : 1) * (reduce ? 0 : 1)
      r.bead.position.set(Math.cos(r.a) * r.r, Math.sin(r.a) * r.r, 0); r.glow.position.copy(r.bead.position)
      r.trail.rotation.z = r.speed > 0 ? r.a - .85 : r.a
      const bc = alarm ? col.crit : col.accent
      r.beadMat.color.copy(bc); r.glow.material.color.copy(bc); r.trailMat.color.copy(bc)
    })

    // clouds drift, and part around the cursor
    for (const sp of puffs) {
      const u = sp.userData
      if (!reduce) { u.base.x += u.drift * dt; if (u.base.x > u.w / 2) u.base.x -= u.w }
      if (mouse.x < 5) {
        v.copy(u.base).project(camera)
        const dx = (v.x - mouse.x) * camera.aspect, dy = v.y - mouse.y, d = Math.hypot(dx, dy)
        if (d < .32) { const f = (1 - d / .32) * .09; u.ox += dx / (d || 1) * f; u.oy += dy / (d || 1) * f }
      }
      u.ox *= .95; u.oy *= .95
      sp.position.set(u.base.x + u.ox, u.base.y + u.oy, u.base.z)
    }
    updateParts(dt, t0)
    renderer.render(scene, camera)
  }
  const loop = () => { if (visible || window.__capture) frame(); raf(loop) }
  // A context can be created and still be lost by the first draw (no GPU, driver reset). Only a scene that drew keeps the
  // poster hidden; otherwise the static poster stays, and the scene is abandoned.
  const start = () => {
    frame()
    if (renderer.getContext().isContextLost()) { renderer.dispose(); return }
    host.classList.add("live"); raf(loop); window.__heroReady = true
  }
  if (cloudTex.image) start(); else new THREE.ImageLoader().load("/assets/landing/cloud.webp", start, undefined, start)
})()

/* ======================= PLAYGROUND: add a host, break it, watch the alert ======================= */
;(() => {
  const stage = root.querySelector("#play"), canvas = stage.querySelector("canvas")
  const renderer = makeRenderer(canvas); if (!renderer) return
  renderer.toneMappingExposure = 1.05
  const scene = new THREE.Scene()
  const pm = new THREE.PMREMGenerator(renderer)
  scene.environment = pm.fromScene(new RoomEnvironment(), 0.04).texture; scene.environmentIntensity = .9
  const camera = new THREE.PerspectiveCamera(34, 1, .1, 100); camera.position.set(0, 7.2, 12.4)
  const controls = new OrbitControls(camera, canvas)
  controls.target.set(0, .8, 0); controls.enableZoom = false; controls.enablePan = false; controls.enableDamping = true
  controls.minPolarAngle = .65; controls.maxPolarAngle = 1.2; controls.minAzimuthAngle = -.7; controls.maxAzimuthAngle = .7; controls.rotateSpeed = .6
  const key = new THREE.DirectionalLight("#ffffff", 1.8); key.position.set(-5, 9, 6); scene.add(key)
  const warm = new THREE.DirectionalLight("#ffc6a6", 1.1); warm.position.set(6, 4, -6); scene.add(warm)

  const css = n => getComputedStyle(root).getPropertyValue(n).trim()
  const C = {}; const readC = () => { for (const k of ["up", "warn", "crit", "stale", "accent", "ink", "surface", "peach"]) C[k] = new THREE.Color(css("--" + k)) }; readC()
  const WHITE = new THREE.Color("#e9e8f2"), BLACK = new THREE.Color("#000000"), EMBER = new THREE.Color("#5a1d08"), DARK_LED = new THREE.Color("#2a2d3a"), RED_OFF = new THREE.Color("#3a1410")

  const plat = new THREE.Mesh(new THREE.CylinderGeometry(6.3, 6.5, .34, 120), new THREE.MeshStandardMaterial({ color: C.surface, roughness: .8, metalness: 0 }))
  plat.position.y = -.17; scene.add(plat)
  const gridTex = (() => {
    const c = document.createElement("canvas"); c.width = c.height = 1024; const g = c.getContext("2d"); g.strokeStyle = "rgba(0,0,0,.14)"; g.lineWidth = 2
    for (let r = 80; r < 512; r += 72) { g.beginPath(); g.arc(512, 512, r, 0, 6.283); g.stroke() }
    for (let a = 0; a < 24; a++) { const c1 = Math.cos(a / 24 * 6.283), s1 = Math.sin(a / 24 * 6.283); g.beginPath(); g.moveTo(512 + c1 * 80, 512 + s1 * 80); g.lineTo(512 + c1 * 508, 512 + s1 * 508); g.stroke() }
    return new THREE.CanvasTexture(c)
  })()
  const gridM = new THREE.Mesh(new THREE.CircleGeometry(6.2, 96), new THREE.MeshBasicMaterial({ map: gridTex, transparent: true, depthWrite: false }))
  gridM.rotation.x = -Math.PI / 2; gridM.position.y = .002; scene.add(gridM)

  // the monitor: a pillar with the trace ring on top
  const TOWER = new THREE.Vector3(0, 0, -2.3), RING_Y = 2.9
  const towerMat = new THREE.MeshStandardMaterial({ color: C.ink, metalness: .5, roughness: .35 })
  const pillar = new THREE.Mesh(new THREE.CylinderGeometry(.16, .26, RING_Y - .3, 32), towerMat); pillar.position.set(TOWER.x, (RING_Y - .3) / 2, TOWER.z); scene.add(pillar)
  const ringMat = new THREE.MeshStandardMaterial({ color: C.accent.clone(), emissive: C.accent.clone(), emissiveIntensity: .35, metalness: .3, roughness: .3 })
  const tRing = new THREE.Mesh(new THREE.TorusGeometry(.62, .06, 20, 80), ringMat); tRing.position.set(TOWER.x, RING_Y, TOWER.z); scene.add(tRing)
  const core = new THREE.Mesh(new THREE.SphereGeometry(.16, 24, 16), new THREE.MeshBasicMaterial({ color: C.up.clone() })); core.position.copy(tRing.position); scene.add(core)
  const coreGlow = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex, color: C.up.clone(), transparent: true, blending: THREE.AdditiveBlending, depthWrite: false })); coreGlow.scale.setScalar(1.1); coreGlow.position.copy(core.position); scene.add(coreGlow)
  const shock = new THREE.Mesh(new THREE.TorusGeometry(.62, .02, 8, 80), new THREE.MeshBasicMaterial({ color: C.crit.clone(), transparent: true, opacity: 0 })); shock.position.copy(tRing.position); shock.rotation.x = Math.PI / 2; scene.add(shock)

  const SLOTS = [new THREE.Vector3(-3.7, 0, 1.0), new THREE.Vector3(-1.35, 0, 2.5), new THREE.Vector3(1.35, 0, 2.5), new THREE.Vector3(3.7, 0, 1.0)]
  const NAMES = ["worker-1", "api-1", "db-1", "cache-1"]
  const hosts = [], pads = []
  const labels = root.querySelector("#labels")
  const bodyGeo = new RoundedBoxGeometry(1.05, 1.35, 1.0, 4, .07)

  const makePad = i => {
    const g = new THREE.Group(); g.position.copy(SLOTS[i])
    const r = new THREE.Mesh(new THREE.RingGeometry(.62, .68, 64), new THREE.MeshBasicMaterial({ color: C.accent.clone(), transparent: true, opacity: .55, side: THREE.DoubleSide }))
    r.rotation.x = -Math.PI / 2; r.position.y = .01; g.add(r)
    const plus = new THREE.Group(); plus.position.y = .55
    const pm2 = new THREE.MeshBasicMaterial({ color: C.accent.clone(), transparent: true, opacity: .8 })
    plus.add(new THREE.Mesh(new THREE.BoxGeometry(.36, .07, .07), pm2), new THREE.Mesh(new THREE.BoxGeometry(.07, .36, .07), pm2))
    g.add(plus); scene.add(g)
    const hit = new THREE.Mesh(new THREE.CylinderGeometry(.7, .7, 1.2, 16), new THREE.MeshBasicMaterial({ visible: false })); hit.position.y = .6; g.add(hit)
    const pad = { i, g, plus, hit }; hit.userData.pad = pad; pads.push(pad); return pad
  }

  const COLORS = { healthy: "var(--up)", cpu: "var(--warn)", mem: "var(--warn)", down: "var(--stale)", alert: "var(--crit)", joining: "var(--accent)" }
  const setLabel = (h, extra) => {
    const txt = { healthy: "healthy", cpu: "cpu climbing", mem: "memory filling", down: "no data", alert: "alert firing", joining: "connecting" }[h.state]
    h.el.innerHTML = `<i style="--c:${COLORS[h.state]}"></i>${h.name} <em>${extra || txt}</em>`
  }
  const makeHost = (i, drop) => {
    const g = new THREE.Group(); g.position.copy(SLOTS[i]); g.rotation.y = -SLOTS[i].x * .12; scene.add(g)
    const mat = new THREE.MeshPhysicalMaterial({ color: WHITE.clone(), metalness: .35, roughness: .32, clearcoat: .6, emissive: BLACK.clone() })
    const body = new THREE.Mesh(bodyGeo, mat); body.position.y = .7; g.add(body)
    const face = new THREE.Mesh(new THREE.PlaneGeometry(.86, 1.12), new THREE.MeshStandardMaterial({ color: "#1b1e2e", roughness: .45, metalness: .3 })); face.position.set(0, .7, .505); g.add(face)
    for (let k = 0; k < 4; k++) { const s = new THREE.Mesh(new THREE.PlaneGeometry(.52, .14), new THREE.MeshStandardMaterial({ map: ventTex, roughness: .5 })); s.position.set(-.1, .32 + k * .23, .507); g.add(s) }
    const ledMat = new THREE.MeshBasicMaterial({ color: C.up.clone() }); const ledM = new THREE.Mesh(new THREE.SphereGeometry(.035, 12, 10), ledMat); ledM.position.set(.32, 1.15, .53); g.add(ledM)
    const ledG = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex, color: C.up.clone(), transparent: true, blending: THREE.AdditiveBlending, depthWrite: false })); ledG.scale.setScalar(.36); ledG.position.copy(ledM.position); g.add(ledG)
    const gBack = new THREE.Mesh(new THREE.PlaneGeometry(.08, .8), new THREE.MeshBasicMaterial({ color: "#2c3044" })); gBack.position.set(.3, .62, .508); g.add(gBack)
    const gFillMat = new THREE.MeshBasicMaterial({ color: C.accent.clone() }); const gFill = new THREE.Mesh(new THREE.PlaneGeometry(.08, .8), gFillMat); gFill.position.set(.3, .62, .509); g.add(gFill)
    const sel = new THREE.Mesh(new THREE.RingGeometry(.78, .84, 64), new THREE.MeshBasicMaterial({ color: C.accent.clone(), transparent: true, opacity: 0, side: THREE.DoubleSide })); sel.rotation.x = -Math.PI / 2; sel.position.y = .012; g.add(sel)
    const hit = new THREE.Mesh(new THREE.BoxGeometry(1.2, 1.6, 1.2), new THREE.MeshBasicMaterial({ visible: false })); hit.position.y = .7; g.add(hit)
    const from = SLOTS[i].clone().add(new THREE.Vector3(0, 1.45, 0)), to = new THREE.Vector3(TOWER.x, RING_Y - .1, TOWER.z)
    const mid = from.clone().lerp(to, .5); mid.y += 1.4
    const curve = new THREE.QuadraticBezierCurve3(from, mid, to)
    const tube = new THREE.TubeGeometry(curve, 64, .022, 6); const linkMat = new THREE.MeshBasicMaterial({ color: C.accent.clone(), transparent: true, opacity: .45 })
    const link = new THREE.Mesh(tube, linkMat); scene.add(link)
    const packets = Array.from({ length: 3 }, (_, k) => { const m = new THREE.Mesh(new THREE.SphereGeometry(.06, 12, 10), new THREE.MeshBasicMaterial({ color: C.up.clone() })); m.userData.t = k / 3; scene.add(m); return m })
    const heatPos = new Float32Array(40 * 3)
    const heat = new THREE.Points(new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(heatPos, 3)), new THREE.PointsMaterial({ map: glowTex, size: .18, color: C.peach.clone(), transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false }))
    heat.userData.seeds = Array.from({ length: 40 }, () => [Math.random() - .5, Math.random(), Math.random() - .5]); g.add(heat)
    const el = document.createElement("div"); el.className = "hl"; labels.append(el)
    const h = { i, name: NAMES[i], g, mat, ledMat, ledG, gFill, gFillMat, sel, hit, linkMat, tube, packets, heat, curve, el, state: "healthy", mem: .42, drop: drop ? 0 : 1, grow: drop ? 0 : 1 }
    hit.userData.host = h; hosts.push(h); setLabel(h); return h
  }

  makeHost(1, false); makeHost(2, false); makePad(0); makePad(3)
  let selected = hosts[0]
  const targetEl = root.querySelector("#target")
  const select = h => { selected = h; hosts.forEach(x => x.el.classList.toggle("sel", x === h)); targetEl.textContent = h.name }
  select(selected)

  const clockT = root.querySelector("#clock-t"), toast = root.querySelector("#toast"), toastT = root.querySelector("#toast-t"), toastP = root.querySelector("#toast-p")
  const term = root.querySelector("#term"), addBtn = root.querySelector("#add"), recoverBtn = root.querySelector("#recover"), score = root.querySelector("#score")
  const tools = [...root.querySelectorAll(".tool")]
  let toastTimer
  const showToast = (title, body, color) => { toast.style.setProperty("--c", color); toastT.textContent = title; toastP.textContent = body; toast.classList.add("show"); clearTimeout(toastTimer); toastTimer = setTimeout(() => toast.classList.remove("show"), 4500) }

  const DETECT = { cpu: 2.2, mem: 3.5, down: 6.5 }, RULE = { cpu: "cpu.high", mem: "memory.high", down: "host.silent" }, LABEL = { cpu: "CPU spike", mem: "Memory leak", down: "Pull the plug" }
  let drill = null, joining = false
  const syncUI = () => {
    tools.forEach(b => b.disabled = !!drill || joining)
    recoverBtn.disabled = !(drill && drill.fired)
    addBtn.disabled = !pads.length || joining || !!drill
    if (!pads.length) addBtn.textContent = "All pads in use"
  }
  tools.forEach(b => b.addEventListener("click", () => {
    if (drill || joining || !selected) return
    const kind = b.dataset.fault
    drill = { kind, host: selected, t: 0, fired: false }
    selected.state = kind; setLabel(selected)
    root.querySelector("#step2").classList.add("done")
    showToast(`Drill started · ${LABEL[kind]}`, `On ${selected.name}. Nobody told UptimeTrace; it has to notice on its own.`, "var(--warn)")
    syncUI()
  }))
  recoverBtn.addEventListener("click", () => {
    if (!drill) return
    const h = drill.host; h.state = "healthy"; setLabel(h)
    showToast(`Resolved · ${RULE[drill.kind]} on ${h.name}`, "The host recovered and the alert closed itself.", "var(--up)")
    drill = null; clockT.textContent = "T+0s"; syncUI()
  })
  const fire = () => {
    const d = drill; d.fired = true; const s = Math.round(DETECT[d.kind] * 10)
    d.host.state = "alert"; setLabel(d.host, `${RULE[d.kind]} · +${s}s`)
    showToast(`Alert · ${RULE[d.kind]} on ${d.host.name}`, `Caught ${s}s after the fault started. One alert, no duplicates.`, "var(--crit)")
    const empty = score.querySelector(".empty"); if (empty) empty.remove()
    const li = document.createElement("li"); li.innerHTML = `<span>${LABEL[d.kind]} · ${d.host.name}</span><span style="color:var(--up)">caught +${s}s</span>`; score.prepend(li)
    root.querySelector("#step3").classList.add("done")
    syncUI()
  }

  const addHost = pad => {
    if (joining || drill || !pad) return
    joining = true; syncUI()
    const name = NAMES[pad.i]
    const l1 = `<span class="dim">$ </span>uptimetrace-agent --token ••••${1000 + Math.floor(Math.random() * 9000)} --name ${name}`
    term.innerHTML = l1
    setTimeout(() => { term.innerHTML = l1 + `\n<span class="dim">registering host…</span>` }, reduce ? 0 : 600)
    setTimeout(() => {
      scene.remove(pad.g); pads.splice(pads.indexOf(pad), 1)
      const h = makeHost(pad.i, true); h.state = "joining"; setLabel(h)
      setTimeout(() => {
        h.state = "healthy"; setLabel(h, "first heartbeat · 4s"); select(h); joining = false
        term.innerHTML = l1 + `\n<span class="dim">registering host…</span>\n<span class="ok">✓ ${name} connected · first heartbeat</span>`
        root.querySelector("#step1").classList.add("done"); syncUI()
        showToast(`${name} is reporting`, "Healthy as soon as fresh data arrived. Now try breaking it.", "var(--up)")
      }, reduce ? 50 : 1900)
    }, reduce ? 50 : 1200)
  }
  addBtn.addEventListener("click", () => addHost(pads[0]))

  const ray = new THREE.Raycaster(), ptr = new THREE.Vector2(); let down = null
  const pick = e => {
    const r = canvas.getBoundingClientRect(); ptr.set((e.clientX - r.left) / r.width * 2 - 1, -((e.clientY - r.top) / r.height * 2 - 1)); ray.setFromCamera(ptr, camera)
    const hit = ray.intersectObjects([...hosts.map(h => h.hit), ...pads.map(p => p.hit)])[0]; return hit && hit.object.userData
  }
  canvas.addEventListener("pointerdown", e => { down = [e.clientX, e.clientY] })
  canvas.addEventListener("pointerup", e => {
    if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 6) return
    const u = pick(e); if (!u) return
    if (u.host && u.host.state !== "joining" && !drill) select(u.host)
    if (u.pad) addHost(u.pad)
  })
  canvas.addEventListener("pointermove", e => { canvas.classList.toggle("point", !!pick(e)) })

  const retheme = () => { readC(); plat.material.color.copy(C.surface); towerMat.color.copy(C.ink) }
  addEventListener("themechange", retheme); matchMedia("(prefers-color-scheme: dark)").addEventListener("change", retheme)

  const size = () => { const w = stage.clientWidth, h = stage.clientHeight; renderer.setSize(w, h, false); camera.aspect = w / h; camera.fov = w < 560 ? 48 : 34; camera.updateProjectionMatrix() }
  size(); new ResizeObserver(size).observe(stage)
  let visible = false; new IntersectionObserver(es => { visible = es[0].isIntersecting }).observe(stage)
  const v = new THREE.Vector3(), clock = new THREE.Clock(); let t = 0
  const ease = x => 1 - Math.pow(1 - x, 3)
  const bounce = x => { const n = 7.5625, d = 2.75; if (x < 1 / d) return n * x * x; if (x < 2 / d) return n * (x -= 1.5 / d) * x + .75; if (x < 2.5 / d) return n * (x -= 2.25 / d) * x + .9375; return n * (x -= 2.625 / d) * x + .984375 }
  const tmp = new THREE.Color()

  const frame = () => {
    const dt = Math.min(clock.getDelta(), .05); t += dt
    controls.update()
    if (drill) { drill.t += dt; if (!drill.fired) clockT.textContent = `T+${Math.round(drill.t * 10)}s`; if (!drill.fired && drill.t >= DETECT[drill.kind]) fire() }
    const alarm = !!(drill && drill.fired)
    tRing.rotation.y += dt * .6; tRing.rotation.x = Math.sin(t * .8) * .15
    tmp.copy(alarm ? C.crit : C.accent); ringMat.color.lerp(tmp, .1); ringMat.emissive.lerp(tmp, .1)
    core.material.color.lerp(alarm ? C.crit : C.up, .1); coreGlow.material.color.copy(core.material.color)
    coreGlow.scale.setScalar(1.1 + (alarm ? Math.sin(t * 10) * .25 : Math.sin(t * 2) * .08))
    if (alarm && !reduce) { const k = (t * .9) % 1; shock.scale.setScalar(1 + k * 2.2); shock.material.opacity = .6 * (1 - k); shock.material.color.copy(C.crit) } else shock.material.opacity = 0
    pads.forEach(p => { p.plus.rotation.y += dt * 1.2; p.plus.position.y = .55 + Math.sin(t * 2 + p.i) * .08 })

    hosts.forEach(h => {
      if (h.drop < 1) h.drop = Math.min(1, h.drop + dt / (reduce ? .01 : .9))
      h.g.position.y = (1 - bounce(h.drop)) * 6
      if (h.drop >= 1 && h.grow < 1) h.grow = Math.min(1, h.grow + dt / (reduce ? .01 : .8))
      h.tube.setDrawRange(0, Math.floor(ease(h.grow) * h.tube.index.count / 3) * 3)
      const mine = drill && drill.host === h ? drill.kind : null
      const hot = mine === "cpu", leaking = mine === "mem", silent = mine === "down", s = h.state
      h.mat.color.lerp(hot ? C.peach : silent && drill.fired ? C.stale : WHITE, .06)
      h.mat.emissive.lerp(hot ? EMBER : BLACK, .06)
      h.g.position.x = SLOTS[h.i].x + (hot && !reduce ? Math.sin(t * 70) * .015 : 0)
      h.mem += ((leaking ? .97 : .42) - h.mem) * (leaking ? .012 : .08)
      h.gFill.scale.y = h.mem; h.gFill.position.y = .62 - .4 * (1 - h.mem)
      h.gFillMat.color.copy(h.mem > .9 ? C.crit : h.mem > .7 ? C.warn : C.accent)
      const ledC = s === "alert" ? (silent ? DARK_LED : Math.sin(t * 12) > 0 ? C.crit : RED_OFF) : silent ? DARK_LED : s === "joining" ? C.accent : (hot || leaking) ? C.warn : C.up
      h.ledMat.color.copy(ledC); h.ledG.material.color.copy(ledC); h.ledG.material.opacity = silent ? 0 : 1
      h.linkMat.opacity += ((silent ? .08 : .45) - h.linkMat.opacity) * .1
      h.linkMat.color.copy(s === "alert" ? C.crit : C.accent)
      h.packets.forEach(p => {
        const live = h.grow >= 1 && !silent && s !== "joining"
        p.visible = live; if (!live) return
        p.userData.t = (p.userData.t + dt * (hot ? .9 : .45)) % 1
        h.curve.getPoint(p.userData.t, p.position)
        p.material.color.copy(s === "alert" ? C.crit : hot || leaking ? C.warn : C.up)
      })
      h.heat.material.opacity += ((hot ? .9 : 0) - h.heat.material.opacity) * .08
      if (h.heat.material.opacity > .01) {
        const a = h.heat.geometry.attributes.position
        h.heat.userData.seeds.forEach((sd, k) => { const y = (sd[1] + t * .5) % 1; a.setXYZ(k, sd[0] * .9, 1.45 + y * 1.2, sd[2] * .9) })
        a.needsUpdate = true
      }
      h.sel.material.opacity += ((h === selected ? .9 : 0) - h.sel.material.opacity) * .15
      v.set(h.g.position.x, h.g.position.y + 2.05, h.g.position.z).project(camera)
      h.el.style.transform = `translate(${Math.round((v.x * .5 + .5) * stage.clientWidth)}px, ${Math.round((-v.y * .5 + .5) * stage.clientHeight)}px) translate(-50%, -100%)`
      h.el.style.opacity = h.drop < .2 ? 0 : 1
    })
    renderer.render(scene, camera)
  }
  const loop = () => { if (visible || window.__capture) frame(); raf(loop) }
  frame(); raf(loop); syncUI()
  window.__play = { addHost: () => addHost(pads[0]), fault: k => root.querySelector(`[data-fault="${k}"]`).click() }
  window.__playReady = true
})()

  return () => {
    alive = false;
    timers.forEach((id) => clearInterval(id));
    renderers.forEach((r) => { try { r.dispose(); r.forceContextLoss && r.forceContextLoss(); } catch {} });
  };
}
