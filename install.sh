#!/usr/bin/env bash
# Link the kit and its skills into Claude Code, and set up the Python environment.
#   ./install.sh            install or refresh
#   ./install.sh --remove   remove the links (leaves the repo alone)
set -euo pipefail
REPO="$(cd "$(dirname "$0")" && pwd)"
SKILLS="$HOME/.claude/skills"
KIT_LINK="$HOME/.claude/disparity-kit"

if [[ "${1:-}" == "--remove" ]]; then
  for d in "$REPO"/skills/*/; do rm -f "$SKILLS/$(basename "$d")"; done
  rm -f "$KIT_LINK"
  echo "removed links"; exit 0
fi

mkdir -p "$SKILLS"
ln -sfn "$REPO" "$KIT_LINK"
for d in "$REPO"/skills/*/; do
  name="$(basename "$d")"
  if [[ -e "$SKILLS/$name" && ! -L "$SKILLS/$name" ]]; then
    echo "skip $name: $SKILLS/$name exists and is not a link"; continue
  fi
  ln -sfn "$REPO/skills/$name" "$SKILLS/$name"
  echo "linked /$name"
done

if [[ ! -x "$REPO/.venv/bin/python" ]]; then
  python3 -m venv "$REPO/.venv"
fi
"$REPO/.venv/bin/pip" install -q -r "$REPO/requirements.txt"
echo "python ready: $REPO/.venv/bin/python"
