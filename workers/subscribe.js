/**
 * Cloudflare Worker — newsletter signup proxy for distillate.dev
 *
 * Accepts POST { email } and creates a global contact in Resend.
 * Deploy: wrangler deploy
 * Secret: wrangler secret put RESEND_API_KEY
 */

const ALLOWED_ORIGINS = ["https://distillate.dev", "http://localhost", "null"];

function corsHeaders(request) {
  const origin = request.headers.get("Origin") || "";
  const allowed = ALLOWED_ORIGINS.some((o) => origin === o || origin.startsWith(o));
  return {
    "Access-Control-Allow-Origin": allowed ? origin : ALLOWED_ORIGINS[0],
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
  };
}

export default {
  async fetch(request, env) {
    // Handle CORS preflight
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(request) });
    }

    if (request.method !== "POST") {
      return Response.json(
        { error: "Method not allowed" },
        { status: 405, headers: corsHeaders(request) },
      );
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return Response.json(
        { error: "Invalid JSON" },
        { status: 400, headers: corsHeaders(request) },
      );
    }

    const email = (body.email || "").trim().toLowerCase();
    if (!email || !email.includes("@")) {
      return Response.json(
        { error: "Valid email required" },
        { status: 400, headers: corsHeaders(request) },
      );
    }

    const SEGMENT_ID = "41c1b7ca-d7ba-4695-8d6d-e9b204f034d6";

    const res = await fetch("https://api.resend.com/contacts", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.RESEND_API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        email,
        unsubscribed: false,
        segments: [{ id: SEGMENT_ID }],
      }),
    });

    if (res.ok) {
      return Response.json(
        { success: true },
        { status: 200, headers: corsHeaders(request) },
      );
    }

    const err = await res.text();
    console.error("Resend error:", res.status, err);
    return Response.json(
      { error: "Could not subscribe" },
      { status: 502, headers: corsHeaders(request) },
    );
  },
};
