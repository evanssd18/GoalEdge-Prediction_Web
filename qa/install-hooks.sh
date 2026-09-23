#!/bin/sh
# Install the GoalEdge AI pre-commit guard into this clone's .git/hooks.
#
#     sh qa/install-hooks.sh
#
# Safe to re-run. Copies qa/pre-commit to .git/hooks/pre-commit and marks it
# executable, so the source guard runs on every commit without anyone having to
# remember it.

set -e

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || {
    echo "install-hooks: not inside a git repository - nothing to install."
    echo "install-hooks: the guard still runs manually via 'python qa/check-sources.py'."
    exit 0
}

src="$repo_root/qa/pre-commit"
dst="$repo_root/.git/hooks/pre-commit"

if [ ! -f "$src" ]; then
    echo "install-hooks: $src not found."
    exit 1
fi

cp "$src" "$dst"
chmod +x "$dst"
echo "install-hooks: installed $dst"
echo "install-hooks: the source guard now runs on every commit."
