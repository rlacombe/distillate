// POST /send-event
// Called by the local app when something email-worthy happens.
// Stores the event and sends an immediate email if user has reports enabled.

import { getServiceClient } from "../_shared/supabase.ts";
import { sendEmail, type Attachment } from "../_shared/resend.ts";

Deno.serve(async (req) => {
  if (req.method !== "POST") {
    return new Response("Method not allowed", { status: 405 });
  }

  const body = await req.json();
  const { auth_token, event_type, payload } = body;

  if (!auth_token || !event_type) {
    return new Response(JSON.stringify({ ok: false, reason: "auth_token and event_type required" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  const db = getServiceClient();

  // Look up user by auth token
  const { data: user, error: userErr } = await db
    .from("users")
    .select("id, email, experiment_reports")
    .eq("auth_token", auth_token)
    .single();

  if (userErr || !user) {
    return new Response(JSON.stringify({ ok: false, reason: "invalid auth_token" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
  }

  // Store event
  const { error: eventErr } = await db.from("events").insert({
    user_id: user.id,
    event_type,
    payload: payload ?? {},
    emailed: false,
  });

  if (eventErr) {
    console.error("Event insert error:", eventErr);
  }

  // Send immediate email for experiment reports
  let emailed = false;
  if (user.experiment_reports && event_type === "experiment_complete") {
    const p = payload ?? {};
    const subject = `Distillate: ${p.project_name || "Experiment"} — ${p.runs || "?"} runs completed`;
    const html = renderExperimentEmail(p);
    emailed = await sendEmail({ to: user.email, subject, html, chartB64: p.chart_png_b64 as string || "" });

    if (emailed) {
      await db.from("events")
        .update({ emailed: true })
        .eq("user_id", user.id)
        .eq("event_type", event_type)
        .order("created_at", { ascending: false })
        .limit(1);

      await db.from("users")
        .update({ last_email_at: new Date().toISOString() })
        .eq("id", user.id);
    }
  }

  return new Response(JSON.stringify({ ok: true, emailed }), {
    headers: { "Content-Type": "application/json" },
  });
});

function renderInsight(text: string): string {
  // Convert markdown-like insight text to HTML
  return text
    .split("\n")
    .map(line => {
      // Bold markers: **text**
      line = line.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
      // Numbered list items
      if (/^\d+\.\s/.test(line)) {
        return `<li style="margin-bottom:8px;">${line.replace(/^\d+\.\s*/, "")}</li>`;
      }
      // Empty lines
      if (!line.trim()) return "";
      return `<p style="font-size:14px;line-height:1.6;margin:0 0 8px;">${line}</p>`;
    })
    .join("")
    // Wrap consecutive <li> in <ol>
    .replace(/(<li[^>]*>.*?<\/li>)+/gs, (match) =>
      `<ol style="font-size:14px;line-height:1.6;padding-left:20px;margin:8px 0 16px;">${match}</ol>`
    );
}

function renderExperimentEmail(p: Record<string, unknown>): string {
  const projectName = p.project_name || "Experiment";
  const runs = p.runs || 0;
  const best = p.best || 0;
  const bestMetric = p.best_metric || "";
  const insight = p.insight || "";
  const githubUrl = p.github_url || "";

  const chartHtml = p.chart_png_b64
    ? `<div style="margin:20px 0;"><img src="cid:chart" alt="Experiment chart" style="width:100%;max-width:560px;border-radius:8px;"></div>`
    : "";

  return `<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>
<body style="font-family:-apple-system,system-ui,BlinkMacSystemFont,sans-serif;max-width:560px;margin:0 auto;padding:24px 20px;color:#333;">
  <div style="margin-bottom:20px;font-size:14px;font-weight:600;color:#333;">⚗️ Distillate</div>
  <h2 style="font-size:18px;font-weight:600;margin:0 0 6px;">${projectName}</h2>
  <p style="font-size:14px;color:#666;margin:0 0 4px;">
    ${runs} run${runs !== 1 ? "s" : ""} completed${best ? ` · ${best} best` : ""}
  </p>
  ${bestMetric ? `<p style="font-size:14px;margin:0 0 4px;"><strong>${bestMetric}</strong></p>` : ""}
  ${chartHtml}
  ${insight ? renderInsight(String(insight)) : ""}
  ${githubUrl ? `<p><a href="${githubUrl}" style="color:#6366f1;font-size:13px;text-decoration:none;">View on GitHub →</a></p>` : ""}
  <div style="border-top:1px solid #eee;margin-top:24px;padding-top:12px;font-size:12px;color:#999;">
    <a href="https://distillate.dev" style="color:#6366f1;text-decoration:none;">distillate.dev</a> · Your research alchemist
  </div>
</body>
</html>`;
}
