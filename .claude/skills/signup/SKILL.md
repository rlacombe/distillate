---
name: signup
description: Sign up for email notifications — experiment reports, paper suggestions, weekly digest
---

# Email Signup

Help the user sign up for email notifications from Distillate.

## Steps

1. Run `distillate --email` via Bash to start the interactive email signup flow
2. The flow will prompt for email address and notification preferences:
   - Experiment reports (when a session finishes)
   - Daily paper suggestions (from their queue)
   - Weekly reading digest
3. After signup, it syncs with the cloud and reports verification status
4. If the user just wants to check their current email settings, tell them to run `distillate --email` — it shows current config if already registered
