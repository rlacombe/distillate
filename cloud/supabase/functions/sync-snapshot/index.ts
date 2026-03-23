// POST /sync-snapshot
// Called by the local app on sync/experiment completion to update cloud state.
// Also handles user registration (first call creates the account).
// Sends a verification email on first registration.

import { getServiceClient } from "../_shared/supabase.ts";
import { sendEmail } from "../_shared/resend.ts";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;

Deno.serve(async (req) => {
  if (req.method !== "POST") {
    return new Response("Method not allowed", { status: 405 });
  }

  const body = await req.json();
  const { email, timezone, cadence, digest_day, preferred_hour, experiment_reports, resend_verification, snapshot } = body;

  if (!email) {
    return new Response(JSON.stringify({ ok: false, reason: "email required" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  const db = getServiceClient();

  // Check if user already exists (to know if this is a new registration)
  const { data: existing } = await db
    .from("users")
    .select("id, verified")
    .eq("email", email)
    .maybeSingle();

  const isNewUser = !existing;
  const shouldSendVerification = isNewUser || (resend_verification && !existing?.verified);

  // Upsert user (creates account on first call)
  const userPayload: Record<string, unknown> = { email };
  if (timezone) userPayload.timezone = timezone;
  if (cadence) userPayload.cadence = cadence;
  if (digest_day !== undefined) userPayload.digest_day = digest_day;
  if (preferred_hour !== undefined) userPayload.preferred_hour = preferred_hour;
  if (experiment_reports !== undefined) userPayload.experiment_reports = experiment_reports;

  const { data: user, error: userErr } = await db
    .from("users")
    .upsert(userPayload, { onConflict: "email" })
    .select("id, auth_token")
    .single();

  if (userErr) {
    console.error("User upsert error:", userErr);
    return new Response(JSON.stringify({ ok: false, reason: userErr.message }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    });
  }

  // Send verification email for new registrations or resend requests
  if (shouldSendVerification) {
    const verifyUrl = `${SUPABASE_URL}/functions/v1/verify?token=${user.auth_token}`;
    await sendEmail({
      to: email,
      subject: "Distillate: Verify your email",
      html: renderVerificationEmail(verifyUrl),
    });
  }

  // Update snapshot if provided
  if (snapshot) {
    const { error: snapErr } = await db.from("snapshots").upsert({
      user_id: user.id,
      papers_read: snapshot.papers_read ?? 0,
      papers_queued: snapshot.papers_queued ?? 0,
      reading_tags: snapshot.reading_tags ?? [],
      recent_highlights: snapshot.recent_highlights ?? [],
      experiments: snapshot.experiments ?? [],
      synced_at: new Date().toISOString(),
    }, { onConflict: "user_id" });

    if (snapErr) {
      console.error("Snapshot upsert error:", snapErr);
    }
  }

  return new Response(JSON.stringify({
    ok: true,
    user_id: user.id,
    auth_token: user.auth_token,
    verified: !isNewUser, // existing users are already verified (or will be soon)
  }), {
    headers: { "Content-Type": "application/json" },
  });
});

function renderVerificationEmail(verifyUrl: string): string {
  return `<div style="font-family:-apple-system,system-ui,sans-serif;max-width:480px;margin:0 auto;padding:32px;background:#1a1a2e;color:#e0e0e0;border-radius:12px;">
  <div style="text-align:center;margin-bottom:20px;">
    <span style="font-size:28px;font-weight:700;color:#e0e0e0;">⚗️ Distillate</span>
  </div>
  <h2 style="color:#e0e0e0;text-align:center;margin:0 0 12px;font-size:20px;">Verify your email</h2>
  <p style="color:#a0a0b0;text-align:center;line-height:1.6;margin:0 0 24px;">Click below to confirm your email and start receiving updates from your research workshop.</p>
  <div style="text-align:center;margin-bottom:24px;">
    <a href="${verifyUrl}" style="display:inline-block;background:#4ade80;color:#0f0f1a;text-decoration:none;padding:12px 32px;border-radius:8px;font-weight:600;font-size:15px;">Verify email</a>
  </div>
  <div style="text-align:center;padding-top:16px;border-top:1px solid #2a2a3e;">
    <a href="https://distillate.dev" style="color:#6366f1;font-size:12px;text-decoration:none;">distillate.dev</a>
  </div>
</div>`;
}
