"""The session pipeline: normalize, route, check, finish, patterns (`process`). Each step is a plain function that takes
and returns Pydantic models (`types`), so it runs the same inline (no Hatchet) and as a Hatchet task
(`sopops.jobs.hatchet`). Rule statuses and the rollup are in `rollup`; how a session is shown to a model in
`transcript`; the procedures a session is checked against in `procedures`."""
