#!/usr/bin/env bash
# Put mp-agent where it is used. Safe to run again after any change.
#   commands   symlinks in ~/.local/bin, so edits here take effect at once
#   launchers  /mp-agent in each AI coding tool you have (Codex: $mp-agent)
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
    echo "mp-agent needs python3 (3.9 or newer). On a Mac: xcode-select --install, or brew install python" >&2
    exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
    echo "mp-agent needs python3 3.9 or newer; this one is $(python3 --version 2>&1)" >&2
    exit 1
fi
if ! command -v git >/dev/null 2>&1; then
    echo "mp-agent needs git" >&2
    exit 1
fi

mkdir -p "$HOME/.local/bin"
for cmd in mp-agent mp-status mp-viz; do
    ln -sfn "$here/bin/$cmd" "$HOME/.local/bin/$cmd"
done
# retired: the version 1 bash loop and its workflow (still in git history)
if [ -L "$HOME/.local/bin/mp-agent-classic" ]; then rm -f "$HOME/.local/bin/mp-agent-classic"; fi
rm -f "$HOME/.cline/workflows/multipass.md"

echo "installed: mp-agent, mp-status and mp-viz in ~/.local/bin"
python3 "$here/bin/mp-agent" launchers --install

case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *)
        echo
        echo "~/.local/bin is not on your PATH yet. Add this line to ~/.zshrc (Mac) or ~/.bashrc (Linux),"
        echo "then open a new terminal:"
        echo '    export PATH="$HOME/.local/bin:$PATH"'
        ;;
esac
echo
echo "next: run mp-viz and follow SETUP"
