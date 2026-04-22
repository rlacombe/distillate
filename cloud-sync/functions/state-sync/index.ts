// POST /state-sync — upload state.json
// GET  /state-sync — download state.json
// Authenticated via auth_token header or query param.

import { getServiceClient } from "../_shared/supabase.ts";

const BUCKET = "state";

Deno.serve(async (req) => {
  const db = getServiceClient();
  const url = new URL(req.url);

  // Auth: token from header or query param
  const token = req.headers.get("x-auth-token") || url.searchParams.get("token");
  if (!token) {
    return json({ ok: false, reason: "auth_token required" }, 401);
  }

  // Look up user
  const { data: user, error: userErr } = await db
    .from("users")
    .select("id, email, verified")
    .eq("auth_token", token)
    .single();

  if (userErr || !user) {
    return json({ ok: false, reason: "invalid auth_token" }, 401);
  }

  const filePath = `${user.id}/state.json`;

  if (req.method === "POST" || req.method === "PUT") {
    // Upload state.json
    const body = await req.text();
    if (!body) {
      return json({ ok: false, reason: "empty body" }, 400);
    }

    const { error } = await db.storage
      .from(BUCKET)
      .upload(filePath, body, {
        contentType: "application/json",
        upsert: true,
      });

    if (error) {
      console.error("Upload error:", error);
      return json({ ok: false, reason: error.message }, 500);
    }

    return json({ ok: true, size: body.length });
  }

  if (req.method === "GET") {
    // Download state.json
    const { data, error } = await db.storage
      .from(BUCKET)
      .download(filePath);

    if (error) {
      if (error.message?.includes("not found") || error.message?.includes("Object not found")) {
        return json({ ok: false, reason: "no state uploaded yet" }, 404);
      }
      console.error("Download error:", error);
      return json({ ok: false, reason: error.message }, 500);
    }

    const text = await data.text();
    return new Response(text, {
      headers: { "Content-Type": "application/json" },
    });
  }

  return json({ ok: false, reason: "method not allowed" }, 405);
});

function json(data: Record<string, unknown>, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
