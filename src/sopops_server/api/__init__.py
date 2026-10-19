"""The HTTP API: one router module per area, mounted in `sopops_server.app.create_app` in this order (the UI last, since it
answers every other path).

runtime (delivery, platform webhooks), auth (sign-in, the org, members, invites), stateless, files, config, releases, serving, sessions, tokens, patterns, review,
suggestions, insights, onboarding, ui. `deps` holds what they share; `errors` maps exceptions to responses."""
