#!/bin/sh
# Show or switch Jarvis's brain on Jarvis, from the Mac. The server restarts in about 2 s.
#   tools/brain.sh          which brain is on
#   tools/brain.sh luna     OpenAI gpt-6-luna, fast tier (the default)
#   tools/brain.sh groq     Groq openai/gpt-oss-20b, low reasoning
#   tools/brain.sh groq openai/gpt-oss-120b   the same brain with another model
#   tools/brain.sh anthropic                  Anthropic claude-opus-5-5, effort low, prompt cache
#   tools/brain.sh anthropic claude-haiku-5-5 the same brain with another model
# The first switch to groq or anthropic copies its key file from the Mac to Jarvis, mode 600, without
# printing it: ~/.config/hallpanel/groq.env (GROQ_API_KEY=...) or anthropic.env (ANTHROPIC_API_KEY=...).
# The anthropic brain also needs the SDK in ~/hallpanel-live/venv, which tools/deploy_jarvis.sh installs.
# Every call sets the brain from scratch: JARVIS_BRAIN_MODEL, JARVIS_BRAIN_EFFORT and JARVIS_BRAIN_TIER
# go back to the brain's defaults, also when the brain stays the same and only the model changes.
set -eu
HOST=${JARVIS_SSH:-user@jarvis.local}
# Plain ssh fails from the Mac (publickey); the shared wrapper uses the Keychain and a control socket.
SSH="$(cd "$(dirname "$0")" && pwd)/jarvis_ssh.sh"
ENV=.config/hallpanel/server.env
case "${1:-}" in
  "") sh "$SSH" "$HOST" "grep '^JARVIS_BRAIN' $ENV || echo 'JARVIS_BRAIN=luna (default)'"; exit 0 ;;
  luna) ;;
  groq) KEY=GROQ_API_KEY FILE=groq.env ;;
  anthropic) KEY=ANTHROPIC_API_KEY FILE=anthropic.env ;;
  *) echo "usage: $0 [luna|groq|anthropic] [model]" >&2; exit 2 ;;
esac
if [ "$1" = anthropic ] && ! sh "$SSH" "$HOST" "hallpanel-live/venv/bin/python3 -c 'import anthropic' 2>/dev/null"; then
  echo "The anthropic package is missing on Jarvis. Run tools/deploy_jarvis.sh first." >&2
  exit 1
fi
if [ "$1" != luna ] && ! sh "$SSH" "$HOST" "grep -q '^$KEY=..' .config/hallpanel/$FILE 2>/dev/null"; then
  if ! grep -q "^$KEY=.." "$HOME/.config/hallpanel/$FILE" 2>/dev/null; then
    echo "Put $KEY=... in ~/.config/hallpanel/$FILE on the Mac first." >&2
    exit 1
  fi
  sh "$SSH" "$HOST" "umask 077 && cat > .config/hallpanel/$FILE" < "$HOME/.config/hallpanel/$FILE"
  echo "copied $FILE to Jarvis"
fi
MODEL=${2:-}
case "$MODEL" in *[!A-Za-z0-9./_:-]*) echo "odd model name: $MODEL" >&2; exit 2 ;; esac
sh "$SSH" "$HOST" "sed -i '/^JARVIS_BRAIN[A-Z_]*=/d' $ENV && echo JARVIS_BRAIN=$1 >> $ENV \
  && { [ -z '$MODEL' ] || echo JARVIS_BRAIN_MODEL=$MODEL >> $ENV; } \
  && systemctl --user restart hallpanel && sleep 2 && systemctl --user is-active hallpanel >/dev/null \
  && echo 'brain: $1 ${MODEL:-(its default model)}'"
