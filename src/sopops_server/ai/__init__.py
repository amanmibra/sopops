"""Model calls. `lm`: a DSPy LM per role (fast, reasoning) from the environment; `signatures`: every typed call;
`system1`: a model gateway's system-1 classifier for decisions; `classify`: Jev-first routing, checks and pattern assignment, escalating to
the reasoning model."""
