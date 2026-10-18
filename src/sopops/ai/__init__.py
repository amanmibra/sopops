"""Model calls. `lm`: a DSPy LM per role (fast, reasoning) from the environment; `signatures`: every typed call;
`jev`: TypeSafe's Jev client (optional); `classify`: Jev-first routing, checks and pattern assignment, escalating to
the reasoning model."""
