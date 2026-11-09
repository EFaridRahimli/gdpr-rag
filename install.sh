#!/bin/sh
# gdpr-rag installer for macOS (and Linux): installs uv, the app, the GDPR index and a launcher.
#
#   From anywhere:            curl -LsSf https://raw.githubusercontent.com/EFaridRahimli/gdpr-rag/main/install.sh | sh
#   Inside an unzipped copy:  sh install.sh
#
# Safe to run again: it updates the code and keeps your chats, documents and key.
set -eu

ZIP_URL="${GDPR_RAG_ZIP:-https://github.com/EFaridRahimli/gdpr-rag/archive/refs/heads/main.zip}"
say() { printf '\n\033[1m%s\033[0m\n' "$1"; }

# 1. uv: brings its own Python and installs every dependency on first run
if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  PATH="$HOME/.local/bin:$PATH"
fi
UV="$(command -v uv)"

# 2. The app: this folder if we're inside a copy, otherwise download into ~/gdpr-rag
if [ -f pyproject.toml ] && grep -q '^name = "gdpr-rag"' pyproject.toml; then
  APP="$(pwd)"
else
  APP="$HOME/gdpr-rag"
  say "Downloading gdpr-rag into $APP..."
  TMP="$(mktemp -d)"
  curl -LsSf "$ZIP_URL" -o "$TMP/app.zip"
  unzip -q "$TMP/app.zip" -d "$TMP"
  mkdir -p "$APP"
  cp -R "$TMP"/gdpr-rag-main/. "$APP"/  # the zip has no data/ or .env, so those are kept
  rm -rf "$TMP"
fi
cd "$APP"

# 3. OpenRouter key, kept in .env (readable only by you)
if ! grep -qs '^OPENROUTER_API_KEY=sk-or-' .env; then
  KEY="${OPENROUTER_API_KEY:-}"
  if [ -z "$KEY" ]; then
    say "Paste your OpenRouter API key (get one at https://openrouter.ai/keys) and press Enter:"
    read -r KEY </dev/tty
  fi
  case "$KEY" in
    sk-or-*) ;;
    *) echo "That doesn't look like an OpenRouter key (they start with sk-or-). Run the installer again."; exit 1 ;;
  esac
  { grep -sv '^OPENROUTER_API_KEY=' .env || true; printf 'OPENROUTER_API_KEY=%s\n' "$KEY"; } > .env.new
  mv .env.new .env
  chmod 600 .env
fi

# 4. Build the GDPR index once (the marker means a half-finished run is retried)
if [ ! -f data/.ingested ]; then
  say "Building the GDPR index. The first run also downloads Python, so allow a few minutes..."
  "$UV" run gdpr-rag ingest
  touch data/.ingested
fi

# 5. Launcher: double-click to start the app and open it in the browser
START="$APP/start.sh"
cat >"$START" <<EOF
#!/bin/sh
# Starts gdpr-rag and opens it in the browser. Close this window to stop the app.
cd "$APP" || exit 1
( until curl -s -o /dev/null http://127.0.0.1:8000/; do sleep 1; done
  if command -v open >/dev/null 2>&1; then open http://localhost:8000; fi ) &
exec "$UV" run gdpr-rag serve
EOF
chmod +x "$START"
if [ "$(uname)" = Darwin ]; then
  LAUNCHER="$HOME/Desktop/Start GDPR RAG.command"
  cp "$START" "$LAUNCHER"
  chmod +x "$LAUNCHER"
  say "Done. Next time, double-click \"Start GDPR RAG\" on your Desktop."
else
  say "Done. Next time, run: $START"
fi

say "Starting gdpr-rag at http://localhost:8000 (close this window to stop it)..."
exec "$START"
