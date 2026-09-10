#!/bin/sh
# BSF Solar Dispatch — one-line installer
#
#   curl -fsSL https://raw.githubusercontent.com/banksiaspringsfarm-rgb/bsf-solar-dispatch-starter/main/install.sh | sh
#
# What it does (and nothing more):
#   1. checks python3 (>=3.8), and git or curl
#   2. installs Claude Code if it isn't on this machine
#   3. downloads the bundle into ~/bsf-solar-dispatch (or $BSF_DIR)
#   4. opens Claude Code in that folder — the included CLAUDE.md runbook takes over
#
# It never touches your Cerbo, plugs or Tuya account. Claude does those steps with you,
# and you can read every one of them in CLAUDE.md before saying yes.
#
# Knobs:  BSF_DIR=~/somewhere   BSF_REPO=<git url or local path>   BSF_REF=main
#         BSF_NO_LAUNCH=1  (download + check only, don't start Claude Code)

set -eu

REPO="${BSF_REPO:-https://github.com/banksiaspringsfarm-rgb/bsf-solar-dispatch-starter.git}"
REF="${BSF_REF:-main}"
DIR="${BSF_DIR:-$HOME/bsf-solar-dispatch}"

say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

say "BSF Solar Dispatch installer"

# ---- 1. prerequisites --------------------------------------------------------
OS="$(uname -s)"
case "$OS" in
  Darwin|Linux) ;;
  *) die "Unsupported OS: $OS. This runs on macOS or Linux (a Raspberry Pi is ideal)." ;;
esac

if ! have python3; then
  die "python3 not found. macOS: 'xcode-select --install' or brew install python. Debian/Pi: sudo apt install python3 python3-pip"
fi
PYV="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)' \
  || die "python3 $PYV is too old; need 3.8 or newer."
say "python3 $PYV ok"

if have node; then say "node $(node --version) ok (flow functions will be syntax-checked)"
else warn "node not found — optional. Without it the installer skips the JS syntax check."; fi

# ---- 2. Claude Code ----------------------------------------------------------
if have claude; then
  say "Claude Code found: $(claude --version 2>/dev/null | head -1)"
else
  say "Claude Code not found — installing (official installer, https://claude.ai/install.sh)"
  if have curl; then curl -fsSL https://claude.ai/install.sh | sh
  elif have wget; then wget -qO- https://claude.ai/install.sh | sh
  else die "need curl or wget to install Claude Code"; fi
  # the installer puts it in ~/.local/bin on most systems
  export PATH="$HOME/.local/bin:$PATH"
  have claude || die "Claude Code installed but not on PATH. Open a new terminal and re-run this script."
  say "Claude Code installed"
fi

# ---- 3. the bundle -----------------------------------------------------------
if [ -d "$DIR/.git" ]; then
  say "Bundle already in $DIR — updating"
  git -C "$DIR" pull --ff-only || warn "could not update (local changes?) — continuing with what's there"
elif [ -e "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
  die "$DIR exists and is not empty. Set BSF_DIR to another folder or move it aside."
else
  if have git; then
    say "Cloning into $DIR"
    git clone --depth 1 --branch "$REF" "$REPO" "$DIR"
  elif have curl; then
    case "$REPO" in
      *github.com*) ;;
      *) die "no git — and can only tarball-download from GitHub. Install git." ;;
    esac
    TARBALL="$(printf '%s' "$REPO" | sed -e 's#\.git$##')/archive/refs/heads/$REF.tar.gz"
    say "No git — downloading $TARBALL"
    mkdir -p "$DIR"
    curl -fsSL "$TARBALL" | tar -xz -C "$DIR" --strip-components=1
  else
    die "need git or curl to download the bundle"
  fi
fi

[ -f "$DIR/CLAUDE.md" ] || die "download looks wrong: $DIR/CLAUDE.md missing"
say "Bundle ready in $DIR"

# python deps for the relay (best effort — the runbook re-checks)
if [ -f "$DIR/publisher/requirements.txt" ]; then
  python3 -m pip install --quiet --user -r "$DIR/publisher/requirements.txt" 2>/dev/null \
    || warn "pip install of publisher deps failed — Claude will retry in Step 0"
fi

# ---- 4. hand over to Claude Code --------------------------------------------
if [ "${BSF_NO_LAUNCH:-0}" = "1" ]; then
  say "BSF_NO_LAUNCH=1 — not starting Claude Code. To install: cd \"$DIR\" && claude"
  exit 0
fi

say "Starting Claude Code in $DIR — it will read the runbook and walk you through the install."
echo
cd "$DIR"
exec claude "Install BSF Solar Dispatch. Start with Step 0.5 (photos) then work through CLAUDE.md in order."
