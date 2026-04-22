// Scheduled function: runs every hour via pg_cron
// Sends daily paper suggestions and weekly digests to users whose local time
// matches their preferred hour.

import { getServiceClient } from "../_shared/supabase.ts";
import { sendEmail } from "../_shared/resend.ts";

Deno.serve(async (req) => {
  // Accept both POST (cron trigger) and GET (manual trigger for testing)
  const db = getServiceClient();
  const now = new Date();

  // Find users whose local time matches their preferred_hour
  // and who have at least one email type enabled
  const { data: users, error } = await db.rpc("get_users_due_for_email");

  if (error) {
    console.error("Error fetching due users:", error);
    return new Response(JSON.stringify({ ok: false, reason: error.message }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    });
  }

  let sent = 0;
  let skipped = 0;

  for (const user of users ?? []) {
    // Get their latest snapshot
    const { data: snapshot } = await db
      .from("snapshots")
      .select("*")
      .eq("user_id", user.id)
      .single();

    if (!snapshot) {
      skipped++;
      continue;
    }

    let sentForUser = false;

    // Daily paper suggestions
    if (user.daily_papers) {
      const subject = "Distillate: Daily paper suggestions";
      const html = renderDailySuggestions(snapshot, user);
      if (await sendEmail({ to: user.email, subject, html })) {
        sent++;
        sentForUser = true;
      }
    }

    // Weekly digest (only on the user's chosen day)
    const todayDow = new Date(now.toLocaleString("en-US", { timeZone: user.timezone })).getDay();
    if (user.weekly_digest && todayDow === user.digest_day) {
      const subject = "Distillate: Weekly research digest";
      const html = renderWeeklyDigest(snapshot, user);
      if (await sendEmail({ to: user.email, subject, html })) {
        sent++;
        sentForUser = true;
      }
    }

    if (sentForUser) {
      await db.from("users")
        .update({ last_email_at: now.toISOString() })
        .eq("id", user.id);
    }
  }

  return new Response(JSON.stringify({ ok: true, sent, skipped, checked: users?.length ?? 0 }), {
    headers: { "Content-Type": "application/json" },
  });
});

function renderDailySuggestions(snapshot: Record<string, unknown>, user: Record<string, unknown>): string {
  const tags = (snapshot.reading_tags as string[]) || [];
  const queued = (snapshot.queued_papers as Record<string, unknown>[]) || [];
  const papersRead = snapshot.papers_read || 0;
  const papersQueued = snapshot.papers_queued || 0;

  // Pick 3 papers from pre-scored suggestions (scored locally at sync time)
  // Weighted random: higher score = more likely to be picked, so emails vary day to day
  const picks: Record<string, unknown>[] = [];
  const pool = [...queued];
  for (let i = 0; i < 3 && pool.length > 0; i++) {
    const totalScore = pool.reduce((s, p) => s + ((p.score as number) || 1), 0);
    let r = Math.random() * totalScore;
    let idx = 0;
    for (let j = 0; j < pool.length; j++) {
      r -= (pool[j].score as number) || 1;
      if (r <= 0) { idx = j; break; }
    }
    picks.push(pool.splice(idx, 1)[0]);
  }

  const pickHtml = picks.length > 0
    ? picks.map(p => {
        const authors = (p.authors as string[]) || [];
        const authorStr = authors.length > 0 ? authors.join(", ") : "";
        const year = (p.year as string) || "";
        const meta = [authorStr, year].filter(Boolean).join(" · ");
        const pTags = (p.tags as string[]) || [];
        const tagHtml = pTags.slice(0, 3).map(t =>
          `<span style="display:inline-block;background:rgba(99,102,241,0.12);color:#818cf8;padding:1px 6px;border-radius:8px;font-size:10px;margin-right:4px;">${t}</span>`
        ).join("");
        const reason = (p.reason as string) || "";
        return `<div style="background:#1a1a2e;border:1px solid #2a2a3e;border-radius:8px;padding:12px 14px;margin-bottom:8px;">
          <div style="font-size:14px;color:#e0e0e8;font-weight:600;margin-bottom:4px;">${p.title || "Untitled"}</div>
          ${meta ? `<div style="font-size:11px;color:#8888a0;margin-bottom:4px;">${meta}</div>` : ""}
          ${reason ? `<div style="font-size:12px;color:#a0a0b8;margin-bottom:6px;">${reason}</div>` : ""}
          ${tagHtml ? `<div>${tagHtml}</div>` : ""}
        </div>`;
      }).join("")
    : `<p style="font-size:13px;color:#8888a0;">No papers in your queue yet. Add some from Zotero!</p>`;

  return `
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>
<body style="background:#0f0f23;color:#e0e0e8;font-family:-apple-system,system-ui,sans-serif;padding:32px 20px;margin:0;">
  <div style="max-width:520px;margin:0 auto;">
    <div style="font-size:13px;color:#8888a0;margin-bottom:16px;">📚 Distillate — Daily Suggestions</div>
    <h1 style="font-size:20px;color:#e0e0e8;margin:0 0 4px;">Today's picks</h1>
    <p style="font-size:13px;color:#8888a0;margin:0 0 16px;">${papersRead} papers read · ${papersQueued} in queue</p>
    ${pickHtml}
    <p style="font-size:13px;color:#a0a0b8;line-height:1.5;margin-top:16px;">
      Open Distillate and ask Nicolas <code style="background:#1a1a2e;padding:2px 6px;border-radius:4px;font-size:12px;">/suggest-papers</code> for more.
    </p>
    <div style="border-top:1px solid #2a2a3e;margin-top:24px;padding-top:16px;font-size:11px;color:#8888a0;">
      <a href="https://distillate.dev" style="color:#6366f1;">distillate.dev</a> · Your research alchemist
    </div>
  </div>
</body>
</html>`;
}

function renderWeeklyDigest(snapshot: Record<string, unknown>, user: Record<string, unknown>): string {
  const papersRead = snapshot.papers_read || 0;
  const queued = snapshot.papers_queued || 0;
  const highlights = (snapshot.recent_highlights as string[]) || [];
  const experiments = (snapshot.experiments as Record<string, unknown>[]) || [];

  const highlightHtml = highlights.length > 0
    ? highlights.slice(0, 3).map(h =>
        `<div style="border-left:3px solid #6366f1;padding:4px 12px;margin-bottom:8px;font-size:13px;color:#a0a0b8;font-style:italic;">"${h.length > 120 ? h.slice(0, 120) + "…" : h}"</div>`
      ).join("")
    : '<div style="font-size:13px;color:#8888a0;">No highlights this week. Read some papers!</div>';

  const expHtml = experiments.length > 0
    ? experiments.slice(0, 5).map((e: Record<string, unknown>) =>
        `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #2a2a3e;font-size:13px;">
          <span style="color:#e0e0e8;">${e.name || "?"}</span>
          <span style="color:#818cf8;">${e.runs || 0} runs${e.best_metric ? ` · ${e.best_metric}` : ""}</span>
        </div>`
      ).join("")
    : "";

  return `
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>
<body style="background:#0f0f23;color:#e0e0e8;font-family:-apple-system,system-ui,sans-serif;padding:32px 20px;margin:0;">
  <div style="max-width:520px;margin:0 auto;">
    <div style="font-size:13px;color:#8888a0;margin-bottom:16px;">⚗️ Distillate — Weekly Digest</div>
    <h1 style="font-size:20px;color:#e0e0e8;margin:0 0 16px;">This week in your lab</h1>

    <div style="background:#1a1a2e;border:1px solid #2a2a3e;border-radius:8px;padding:14px 16px;margin-bottom:20px;">
      <div style="display:flex;gap:24px;">
        <div><div style="font-size:22px;color:#fbbf24;font-weight:700;">${papersRead}</div><div style="font-size:11px;color:#8888a0;">papers read</div></div>
        <div><div style="font-size:22px;color:#818cf8;font-weight:700;">${queued}</div><div style="font-size:11px;color:#8888a0;">in queue</div></div>
        <div><div style="font-size:22px;color:#34d399;font-weight:700;">${experiments.length}</div><div style="font-size:11px;color:#8888a0;">experiments</div></div>
      </div>
    </div>

    ${highlights.length > 0 ? `<div style="margin-bottom:20px;">
      <div style="font-size:13px;color:#8888a0;margin-bottom:8px;">Recent highlights</div>
      ${highlightHtml}
    </div>` : ""}

    ${expHtml ? `<div style="margin-bottom:20px;">
      <div style="font-size:13px;color:#8888a0;margin-bottom:8px;">Experiments</div>
      ${expHtml}
    </div>` : ""}

    <p style="font-size:13px;color:#a0a0b8;line-height:1.5;">
      Open Distillate to dive deeper. Ask Nicolas <code style="background:#1a1a2e;padding:2px 6px;border-radius:4px;font-size:12px;">/scan-experiments</code> for a full lab report.
    </p>
    <div style="border-top:1px solid #2a2a3e;margin-top:24px;padding-top:16px;font-size:11px;color:#8888a0;">
      <a href="https://distillate.dev" style="color:#6366f1;">distillate.dev</a> · Your research alchemist
    </div>
  </div>
</body>
</html>`;
}
