import { describe, expect, it, vi } from "vitest";
import { ApiError, createApiClient } from "./client";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

function setup(responses: Response[] | (() => Promise<Response>)) {
  const queue = Array.isArray(responses) ? [...responses] : null;
  const fetchImpl = vi.fn(async (..._a: Parameters<typeof fetch>) => (queue ? queue.shift()! : (responses as () => Promise<Response>)()));
  const getToken = vi.fn(async (o?: { skipCache?: boolean }) => (o?.skipCache ? "fresh-token" : "cached-token"));
  const onUnauthorized = vi.fn();
  const client = createApiClient({ baseUrl: "", getToken, onUnauthorized, fetchImpl: fetchImpl as unknown as typeof fetch });
  return { client, fetchImpl, getToken, onUnauthorized };
}

const auth = (call: unknown[]) => ((call[1] as RequestInit).headers as Record<string, string>).Authorization;

describe("api client", () => {
  it("sends the session token as a bearer header and builds query strings from filters", async () => {
    const { client, fetchImpl } = setup([json(200, { items: [] })]);
    await client.hosts({ source: ["live", "replay"], health: [], search: "web", page_size: 1 });
    const [url] = fetchImpl.mock.calls[0]!;
    expect(url).toBe("/api/v1/hosts?source=live%2Creplay&search=web&page_size=1");
    expect(auth(fetchImpl.mock.calls[0]!)).toBe("Bearer cached-token");
  });

  it("on 401 refreshes the token once and retries, without signing the user out", async () => {
    const { client, fetchImpl, getToken, onUnauthorized } = setup([
      json(401, { error: { code: "unauthorized", message: "A valid session token is required." } }),
      json(200, { user_id: "user_1", authorized: true }),
    ]);
    await expect(client.me()).resolves.toEqual({ user_id: "user_1", authorized: true });
    expect(getToken).toHaveBeenLastCalledWith({ skipCache: true });
    expect(auth(fetchImpl.mock.calls[1]!)).toBe("Bearer fresh-token");
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("ends the session when a freshly refreshed token is still rejected", async () => {
    const e401 = () => json(401, { error: { code: "unauthorized", message: "A valid session token is required." } });
    const { client, fetchImpl, onUnauthorized } = setup([e401(), e401()]);
    await expect(client.me()).rejects.toMatchObject({ status: 401, code: "unauthorized" });
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("maps the API error envelope, including field details, to ApiError", async () => {
    const { client } = setup([json(422, { error: { code: "invalid_request", message: "bad", details: [{ path: "start", message: "start must be earlier than end." }] } })]);
    const err = await client.series("h", { metric: "system.cpu.utilization" }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 422, code: "invalid_request", details: [{ path: "start" }] });
  });

  it("does not retry a 403 (authenticated but not allowed) and does not sign out", async () => {
    const { client, fetchImpl, onUnauthorized } = setup([json(403, { error: { code: "forbidden", message: "no" } })]);
    await expect(client.hosts()).rejects.toMatchObject({ status: 403 });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("reports an unreachable API as a network error and encodes host ids in paths", async () => {
    const { client, fetchImpl } = setup(async () => { throw new TypeError("Failed to fetch"); });
    await expect(client.host("a/b c")).rejects.toMatchObject({ status: 0, code: "network_error", isNetwork: true });
    expect(fetchImpl.mock.calls[0]![0]).toBe("/api/v1/hosts/a%2Fb%20c");
  });

  it("sends mutations with the right method, a JSON body and the bearer token, and surfaces API error details", async () => {
    const { client, fetchImpl } = setup([
      json(201, { id: "c1", name: "n", agent_id: "a", token: "utk_x", created_at: "t", created_by: "u", revoked_at: null, revoked_by: null }),
      json(200, { id: "c1" }),
      json(409, { error: { code: "credential_limit", message: "At most 5 active credentials per host; revoke one first." } }),
    ]);
    await client.issueCredential("web 01", { name: "n" });
    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(url).toBe("/api/v1/registry/hosts/web%2001/credentials");
    expect((init as RequestInit).method).toBe("POST");
    expect((init as RequestInit).body).toBe(JSON.stringify({ name: "n" }));
    expect(((init as RequestInit).headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(auth(fetchImpl.mock.calls[0]!)).toBe("Bearer cached-token");
    await client.revokeCredential("web-01", "c1");
    expect((fetchImpl.mock.calls[1]![1] as RequestInit).method).toBe("DELETE");
    expect((fetchImpl.mock.calls[1]![1] as RequestInit).body).toBeUndefined();
    await expect(client.issueCredential("web-01", {})).rejects.toMatchObject({ status: 409, code: "credential_limit" });
  });
});
