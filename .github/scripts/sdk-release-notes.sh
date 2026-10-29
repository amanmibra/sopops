#!/usr/bin/env bash
# Prints the notes for a lockstep SDK release: the release PR's body, and later the GitHub Release's.
#
#   .github/scripts/sdk-release-notes.sh 0.6.0 release/sdk-v0.6.0   # up to the release branch
#   .github/scripts/sdk-release-notes.sh 0.6.0 sdk-v0.6.0           # up to the tag
#
# GitHub's generated notes list merged pull requests, and most SDK changes land as direct commits, so the
# notes are the commits that touched each SDK since its previous release. Before the first lockstep
# release that was its own py-v or node-v tag, so each section starts from the newest of those or sdk-v.
set -euo pipefail

version=${1:?version, like 0.6.0}
ref=${2:?git ref the release ends at}
repo=${GITHUB_REPOSITORY:-sopops-dev/sopops}

# Newest first by date: versions only go up, and the prefixes don't sort against each other by name.
previous() { git tag -l "$@" --sort=-creatordate | grep -vx "sdk-v$version" | head -n1; }

section() {  # sdk, label, prefix
  local prev commits
  prev=$(previous "sdk-v*" "$3*")
  commits=$(git log --format='- %s (%h)' --invert-grep --grep='^Release ' "$prev..$ref" -- "sdks/$1")
  printf '## %s, since %s\n\n%s\n\n' "$2" "$prev" "${commits:-- No changes besides the version.}"
}

# shellcheck disable=SC2016  # the backticks are Markdown
printf '`pip install sopops==%s` and `npm install sopops@%s`\n\n' "$version" "$version"
section python "Python (sdks/python)" py-v
section node "TypeScript (sdks/node)" node-v
printf '**Full changelog**: https://github.com/%s/compare/%s...%s\n' "$repo" "$(previous "sdk-v*" "py-v*" "node-v*")" "$ref"
