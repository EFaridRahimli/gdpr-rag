#!/bin/sh
# gdpr-rag installer for macOS (and Linux): installs uv, the app and a launcher.
# In the app's Settings: sign in with ChatGPT and add an OpenRouter key; the GDPR index then builds itself.
#
#   From anywhere:            curl -LsSf https://raw.githubusercontent.com/EFaridRahimli/gdpr-rag/main/install.sh | sh
#   Inside an unzipped copy:  sh install.sh
#
# Safe to run again: it updates the code and keeps your chats, documents and settings.
set -eu

ZIP_URL="${GDPR_RAG_ZIP:-https://github.com/EFaridRahimli/gdpr-rag/archive/refs/heads/main.zip}"
say() { printf '\n\033[1m%s\033[0m\n' "$1"; }

# 1. uv: brings its own Python and installs every dependency on first run
if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv..."
  UV_SETUP="$(mktemp)"  # download first: with curl | sh a failed download would pass silently
  if ! curl -LsSf https://astral.sh/uv/install.sh -o "$UV_SETUP" || ! sh "$UV_SETUP"; then
    echo "Couldn't install uv. Check your internet connection and run the installer again."
    exit 1
  fi
  rm -f "$UV_SETUP"
  PATH="$HOME/.local/bin:$PATH"
fi
if ! UV="$(command -v uv)"; then
  echo "uv was installed but isn't on PATH. Open a new Terminal window and run the installer again."
  exit 1
fi

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

# 3. Launcher: double-click to start the app and open it in the browser
START="$APP/start.sh"
cat >"$START" <<EOF
#!/bin/sh
# Starts gdpr-rag and opens it in the browser. Close this window to stop the app.
cd "$APP" || exit 1
( n=0
  until curl -s -o /dev/null http://127.0.0.1:8000/; do
    n=\$((n + 1))
    if [ \$n -ge 300 ]; then echo "gdpr-rag did not start. Is port 8000 already in use?"; exit 1; fi
    sleep 1
  done
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

say "Starting gdpr-rag at http://localhost:8000. The first start downloads Python, so allow a few minutes."
say "In the app, open Settings: sign in with ChatGPT (Plus or Pro) and paste an OpenRouter key for search (https://openrouter.ai/keys). Close this window to stop the app."
exec "$START"
