#!/bin/zsh
emulate -L zsh
setopt no_unset pipe_fail

REPO="MathematicLove/homebrew-converter"
BRANCH="${CONVERTER_BRANCH:-main}"
PREFIX="${CONVERTER_PREFIX:-$HOME/.local/share/macos-converter}"
APP="$PREFIX/app"
INIT="$PREFIX/init.zsh"
BIN_DIR="${CONVERTER_BIN_DIR:-$HOME/.local/bin}"
ZSHRC="${ZDOTDIR:-$HOME}/.zshrc"
SOURCE_LINE="[[ -f \"$INIT\" ]] && source \"$INIT\""

say() { print -r -- "==> $*"; }
die() { print -u2 -r -- "Error: $*"; exit 1; }

remove_rc_line() {
  [[ -f "$ZSHRC" ]] || return 0
  local tmp="$ZSHRC.converter.tmp"
  grep -vF "$INIT" "$ZSHRC" > "$tmp"
  cat "$tmp" > "$ZSHRC"
  rm -f "$tmp"
}

uninstall() {
  say "Removing convert"
  if [[ -L "$BIN_DIR/convert" && "$(readlink "$BIN_DIR/convert")" == "$APP/"* ]]; then
    rm -f "$BIN_DIR/convert"
  fi
  remove_rc_line
  rm -rf "$PREFIX"
  say "Done. Open a new terminal window."
}

[[ "$(uname -s)" == "Darwin" ]] || die "convert works on macOS only"

if [[ "${1:-}" == "--uninstall" ]]; then
  uninstall
  exit 0
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

SRC="${CONVERTER_SRC:-}"
if [[ -z "$SRC" && "${0:t}" == "install.sh" && -f "$0" && -f "${0:A:h}/bin/convert" && -f "${0:A:h}/libexec/convert_core.py" ]]; then
  SRC="${0:A:h}"
fi

if [[ -n "$SRC" ]]; then
  say "Installing from $SRC"
else
  say "Downloading $REPO ($BRANCH)"
  curl -fsSL "https://github.com/$REPO/archive/refs/heads/$BRANCH.tar.gz" -o "$WORK/src.tar.gz" || die "download failed"
  tar -xzf "$WORK/src.tar.gz" -C "$WORK" || die "cannot unpack the archive"
  SRC="$(print -r -- "$WORK"/*(/N[1]))"
  [[ -n "$SRC" ]] || die "unexpected archive layout"
fi

for f in bin/convert libexec/convert_core.py completions/_convert; do
  [[ -f "$SRC/$f" ]] || die "$f is missing in the source"
done

rm -rf "$APP"
mkdir -p "$APP/bin" "$APP/libexec" "$APP/completions" "$BIN_DIR" || die "cannot create $APP"
cp "$SRC/bin/convert" "$APP/bin/convert"
cp "$SRC/libexec/convert_core.py" "$APP/libexec/convert_core.py"
cp "$SRC/completions/_convert" "$APP/completions/_convert"
chmod +x "$APP/bin/convert"

if [[ -e "$BIN_DIR/convert" && ! -L "$BIN_DIR/convert" ]]; then
  die "$BIN_DIR/convert already exists and is not a link. Remove it or set CONVERTER_BIN_DIR"
fi
ln -sf "$APP/bin/convert" "$BIN_DIR/convert"

cat > "$INIT" <<EOF
path=("$BIN_DIR" \${path:#$BIN_DIR})
fpath=("$APP/completions" \${fpath:#$APP/completions})
if (( ! \$+functions[compdef] )); then
  autoload -Uz compinit && compinit
fi
autoload -Uz _convert && compdef _convert convert
EOF

touch "$ZSHRC"
if ! grep -qF "$INIT" "$ZSHRC"; then
  print -r -- "" >> "$ZSHRC"
  print -r -- "$SOURCE_LINE" >> "$ZSHRC"
fi

if [[ -z "${CONVERTER_SKIP_SETUP:-}" ]]; then
  "$APP/bin/convert" --setup || die "dependency setup failed, run later: convert --setup"
fi

say "Installed. Open a new terminal window (or run: source $ZSHRC) and try: convert -h"
