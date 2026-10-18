"""The HTTP API: one router module per area, mounted in `sopops.app.create_app` in this order (the UI last, since it
answers every other path).

runtime (delivery, platform webhooks), stateless, files, config, releases, serving, sessions, tokens, patterns,
insights, onboarding, ui. `deps` holds what they share; `errors` maps exceptions to responses."""
