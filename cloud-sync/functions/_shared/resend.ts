// Shared Resend email helper for all Edge Functions

const RESEND_API_KEY = Deno.env.get("RESEND_API_KEY")!;
const FROM_EMAIL = Deno.env.get("FROM_EMAIL") || "⚗️ Nicolas <nicolas@distillate.dev>";

export interface EmailOptions {
  to: string;
  subject: string;
  html: string;
  chartB64?: string; // base64 PNG for inline chart
}

export async function sendEmail({ to, subject, html, chartB64 }: EmailOptions): Promise<boolean> {
  try {
    // Inject chart as inline data URI
    if (chartB64) {
      html = html.replace("cid:chart", `data:image/png;base64,${chartB64}`);
    }
    const payload: Record<string, unknown> = { from: FROM_EMAIL, to, subject, html };
    const res = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${RESEND_API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      console.error(`Resend error: ${res.status} ${await res.text()}`);
      return false;
    }
    return true;
  } catch (err) {
    console.error("Failed to send email:", err);
    return false;
  }
}
