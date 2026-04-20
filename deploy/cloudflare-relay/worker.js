// Cloudflare Worker — anonymous comment relay for advisor-ledger.
//
// Flow:
//   1. Browser POSTs {pathname, comment, token} to this Worker.
//   2. Worker verifies the Turnstile token.
//   3. Worker finds (or creates) the GitHub Issue whose title matches the
//      pathname (same convention utterances uses) and posts the comment
//      using the PAT stored as a secret.
//
// Environment variables (set in the Worker dashboard → Settings → Variables):
//   REPO                  "the-hidden-fish/advisor-ledger"        (plain)
//   ALLOWED_ORIGIN        "https://the-hidden-fish.github.io"      (plain)
//   TURNSTILE_SECRET      (secret, from Turnstile site)
//   GH_TOKEN              (secret, fine-grained PAT with issues:write on the repo)
//
// Deploy: paste this whole file as the Worker "module" script and hit Deploy.

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") return cors(env, new Response(null, { status: 204 }));
    if (request.method !== "POST") return cors(env, new Response("method not allowed", { status: 405 }));

    let payload;
    try {
      payload = await request.json();
    } catch {
      return cors(env, new Response("bad request", { status: 400 }));
    }
    const { pathname, comment, token } = payload || {};
    if (!pathname || !comment || !token) return cors(env, new Response("missing fields", { status: 400 }));
    if (typeof pathname !== "string" || pathname.length > 200) return cors(env, new Response("bad pathname", { status: 400 }));
    if (typeof comment !== "string" || comment.trim().length < 3 || comment.length > 4000)
      return cors(env, new Response("comment length out of range (3–4000 chars)", { status: 400 }));

    // Cheap spam heuristic: cap link count.
    const linkCount = (comment.match(/https?:\/\//gi) || []).length;
    if (linkCount > 3) return cors(env, new Response("too many links", { status: 400 }));

    // Verify Turnstile.
    const ip = request.headers.get("cf-connecting-ip") || "";
    const ts = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ secret: env.TURNSTILE_SECRET, response: token, remoteip: ip }),
    });
    const tsData = await ts.json().catch(() => ({ success: false }));
    if (!tsData.success) return cors(env, new Response("captcha failed", { status: 403 }));

    // Find or create the Issue for this pathname.
    const repo = env.REPO;
    const ghHeaders = {
      authorization: `Bearer ${env.GH_TOKEN}`,
      accept: "application/vnd.github+json",
      "user-agent": "advisor-ledger-relay/1.0",
    };
    const q = encodeURIComponent(`repo:${repo} label:comments in:title "${pathname}"`);
    const searchResp = await fetch(`https://api.github.com/search/issues?q=${q}`, { headers: ghHeaders });
    if (!searchResp.ok) return cors(env, new Response(`search failed: ${searchResp.status}`, { status: 502 }));
    const searchData = await searchResp.json();
    const exact = (searchData.items || []).find((i) => i.title === pathname && !i.pull_request);
    let issueNumber;
    if (exact) {
      issueNumber = exact.number;
    } else {
      const createResp = await fetch(`https://api.github.com/repos/${repo}/issues`, {
        method: "POST",
        headers: { ...ghHeaders, "content-type": "application/json" },
        body: JSON.stringify({
          title: pathname,
          body: `Discussion thread for [\`${pathname}\`](https://${env.ALLOWED_ORIGIN.replace("https://", "")}${pathname}). Anonymous comments arrive via the relay Worker; logged-in comments arrive via utterances.`,
          labels: ["comments"],
        }),
      });
      if (!createResp.ok) return cors(env, new Response(`issue create failed: ${createResp.status}`, { status: 502 }));
      const created = await createResp.json();
      issueNumber = created.number;
    }

    // Post the comment as a quoted block so downstream readers can tell it's relayed.
    const pseudoId = await dayIpHash(ip);
    const safeBody = comment.replace(/\r\n/g, "\n").replace(/^/gm, "> ");
    const commentBody = `_(匿名 · day-hash \`${pseudoId}\`)_\n\n${safeBody}`;
    const postResp = await fetch(`https://api.github.com/repos/${repo}/issues/${issueNumber}/comments`, {
      method: "POST",
      headers: { ...ghHeaders, "content-type": "application/json" },
      body: JSON.stringify({ body: commentBody }),
    });
    if (!postResp.ok) return cors(env, new Response(`comment failed: ${postResp.status}`, { status: 502 }));
    const c = await postResp.json();
    return cors(
      env,
      new Response(JSON.stringify({ ok: true, url: c.html_url, issue: issueNumber }), {
        status: 200,
        headers: { "content-type": "application/json" },
      })
    );
  },
};

function cors(env, resp) {
  const r = new Response(resp.body, resp);
  r.headers.set("access-control-allow-origin", env.ALLOWED_ORIGIN || "*");
  r.headers.set("access-control-allow-methods", "POST, OPTIONS");
  r.headers.set("access-control-allow-headers", "content-type");
  r.headers.set("vary", "origin");
  return r;
}

async function dayIpHash(ip) {
  if (!ip) return "anon";
  const day = new Date().toISOString().slice(0, 10);
  const data = new TextEncoder().encode(ip + "|" + day);
  const hash = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(hash))
    .slice(0, 4)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
