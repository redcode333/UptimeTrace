import { siClaude, siCursor, siGithubcopilot, siGooglegemini, siWindsurf } from "simple-icons";
import openaiSvg from "@lobehub/icons-static-svg/icons/openai.svg?raw";

/**
 * Logos for the "Works with ... over MCP" roller. Every path comes from a published icon package (simple-icons for the
 * brands it has, @lobehub/icons-static-svg for ChatGPT, which simple-icons does not carry). Nothing here is hand-drawn.
 * Brand colours that are black (or near-black) are drawn in currentColor, so they follow the theme.
 */
type Agent = { name: string; viewBox: string; body: string; hex: string };

const isNearBlack = (hex: string) => {
  const n = parseInt(hex, 16);
  return ((n >> 16) & 255) + ((n >> 8) & 255) + (n & 255) < 90;
};

const fromSimpleIcon = (name: string, icon: { path: string; hex: string }): Agent => ({
  name, viewBox: "0 0 24 24", body: `<path d="${icon.path}"/>`, hex: icon.hex,
});

/** The lobehub SVG is a complete <svg> document; keep its viewBox and its inner markup, without its own fills. */
function fromStaticSvg(name: string, svg: string): Agent {
  const viewBox = /viewBox="([^"]+)"/.exec(svg)?.[1] ?? "0 0 24 24";
  const inner = svg.slice(svg.indexOf(">", svg.indexOf("<svg")) + 1, svg.lastIndexOf("</svg>")).replace(/\sfill="[^"]*"/g, "").replace(/<title>[\s\S]*?<\/title>/g, "");
  return { name, viewBox, body: inner, hex: "000000" };
}

const AGENTS: Agent[] = [
  fromSimpleIcon("Claude", siClaude),
  fromStaticSvg("ChatGPT", openaiSvg),
  fromSimpleIcon("Cursor", siCursor),
  fromSimpleIcon("GitHub Copilot", siGithubcopilot),
  fromSimpleIcon("Windsurf", siWindsurf),
  fromSimpleIcon("Gemini CLI", siGooglegemini),
];

export const MCP_AGENT_NAMES = AGENTS.map((a) => a.name);

const item = (a: Agent) => {
  const fill = isNearBlack(a.hex) ? "currentColor" : `#${a.hex}`;
  return `<span class="ro"><svg width="18" height="18" viewBox="${a.viewBox}" fill="${fill}" aria-hidden="true">${a.body}</svg>${a.name}</span>`;
};

/**
 * The roller's moving track: the six agents, then a copy of the first so the loop is seamless.
 * Returned as markup for the static landing string; the runtime rolls it.
 */
export const MCP_ROLLER_TRACK = AGENTS.concat(AGENTS.slice(0, 1)).map(item).join("");
