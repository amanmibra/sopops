# sopops API

The API reference lives at [docs.sopops.dev/api](https://docs.sopops.dev/api/), and in [docs/api/](docs/api/) in this repo. A running sopops also serves the OpenAPI spec at `/openapi.json`, and `/docs` lets you try each endpoint.

- [Overview](docs/api/index.md): authentication, errors, and the pages below
- [Agents and config](docs/api/agents-and-config.md): agents, shared instructions, procedures, groups, files, stateless sop checks
- [Releases](docs/api/releases.md): publishing, serving instructions at call start, pushing to ElevenLabs or Vapi
- [Sessions](docs/api/sessions.md): sending calls, streaming, backfill, [the canonical format](docs/api/sessions.md#the-canonical-format), importing a file
- [Checks and review](docs/api/checks-and-review.md): SOP checks, grouped mistakes, verdicts, suggestions, rule lineage
- [Insights](docs/api/insights.md): pass rates, release compare, A/B tests, model spend
- [Teams and access](docs/api/teams-and-access.md): sign-in, teams, tokens, AI keys and models
- [Import](docs/api/import.md): importing agents, and the coding agent's endpoints
- [GitHub](docs/api/github.md): connecting a repository, sync, the GitHub App
