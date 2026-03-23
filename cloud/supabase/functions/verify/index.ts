// GET /verify?token=<auth_token>
// Magic link handler: marks user as verified, then redirects to distillate.dev confirmation page.

import { getServiceClient } from "../_shared/supabase.ts";

Deno.serve(async (req) => {
  const url = new URL(req.url);
  const token = url.searchParams.get("token");

  if (!token) {
    return redirect("invalid");
  }

  const db = getServiceClient();

  // Find user by auth_token
  const { data: user, error } = await db
    .from("users")
    .select("id, email, verified")
    .eq("auth_token", token)
    .single();

  if (error || !user) {
    return redirect("invalid");
  }

  if (user.verified) {
    return redirect("already");
  }

  // Mark as verified
  const { error: updateErr } = await db
    .from("users")
    .update({ verified: true })
    .eq("id", user.id);

  if (updateErr) {
    console.error("Verify update error:", updateErr);
    return redirect("error");
  }

  return redirect("ok");
});

function redirect(status: string): Response {
  return new Response(null, {
    status: 302,
    headers: { "Location": `https://distillate.dev/verified?status=${status}` },
  });
}
