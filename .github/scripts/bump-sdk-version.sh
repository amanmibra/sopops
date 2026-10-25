#!/usr/bin/env bash
# Bumps both SDKs to one shared version and prints it. Used by release-sdk.yml; run it locally to preview
# a bump (then `git checkout -- sdks` to undo). Fails if the version's tag already exists.
#
#   .github/scripts/bump-sdk-version.sh patch|minor|major
#
# The two SDKs ship the same features under the same version. The next version is the bump of the higher
# current one, so if they ever drift apart (0.5.1 and 0.6.0), the next release brings them back together.
# Each current version must already be released: otherwise a hand bump that never shipped would be
# skipped over silently.
set -euo pipefail

bump=${1:?bump: patch, minor or major}
root=$(git rev-parse --show-toplevel)

py=$(sed -n 's/^version = "\(.*\)"$/\1/p' "$root/sdks/python/pyproject.toml" | head -n1)
node=$(node -p 'require(process.argv[1]).version' "$root/sdks/node/package.json")

tagged() { git -C "$root" rev-parse -q --verify "refs/tags/$1" >/dev/null; }

for pair in "python $py py-v" "node $node node-v"; do
  read -r sdk version prefix <<<"$pair"
  if ! [[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "$sdk version '$version' is not plain MAJOR.MINOR.PATCH; fix it by hand" >&2
    exit 1
  fi
  # Releases up to 0.5.0 were tagged per SDK (py-v, node-v); from the next one on, sdk-v.
  if ! tagged "sdk-v$version" && ! tagged "$prefix$version"; then
    echo "$sdk is at $version but neither sdk-v$version nor $prefix$version is tagged; release or revert it first" >&2
    exit 1
  fi
done

current=$(printf '%s\n%s\n' "$py" "$node" | sort -t. -k1,1n -k2,2n -k3,3n | tail -n1)
IFS=. read -r major minor patch <<<"$current"
case $bump in
  patch) next=$major.$minor.$((patch + 1)) ;;
  minor) next=$major.$((minor + 1)).0 ;;
  major) next=$((major + 1)).0.0 ;;
  *) echo "unknown bump: $bump" >&2; exit 2 ;;
esac

for tag in "sdk-v$next" "py-v$next" "node-v$next"; do
  if tagged "$tag"; then echo "tag $tag already exists" >&2; exit 1; fi
done

# Only the [project] version line: the first `version = ` in the file.
sed -i.bak "1,/^version = /s/^version = \".*\"$/version = \"$next\"/" "$root/sdks/python/pyproject.toml"
sed -i.bak "s/^__version__ = \".*\"$/__version__ = \"$next\"/" "$root/sdks/python/src/sopops/__init__.py"
rm -f "$root/sdks/python/pyproject.toml.bak" "$root/sdks/python/src/sopops/__init__.py.bak"
# uv.lock records the project's own version; `uv lock` rewrites it without upgrading anything else.
(cd "$root/sdks/python" && uv lock --quiet)
# pnpm-lock.yaml does not record the root package's version, so package.json is the only Node file.
(cd "$root/sdks/node" && npm pkg set "version=$next" 2>/dev/null)

echo "$next"
