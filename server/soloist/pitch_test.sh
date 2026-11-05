#!/bin/bash
# Hear what 45 rpm sounds like: Spotify through a temporary pitch shifter for a few seconds, then back.
# Run on Jarvis: bash server/soloist/pitch_test.sh [seconds]. Needs rubberband-ladspa (apt). It loads the filter
# only while it runs (pw-cli -m), moves the Soloist streams there and back, and changes nothing permanently.
# +5 semitones 20 cents is 45/33 1/3. Tried 2026-10-28 (docs/spotify.md).
SECONDS_PITCHED=${1:-12}
ARGS='{ node.description = "JBL 45 varv" media.name = "JBL 45 varv"
  filter.graph = { nodes = [ { type = ladspa name = rb plugin = "ladspa-rubberband" label = "rubberband-pitchshifter-stereo"
      control = { "Semitones" = 5 "Cents" = 20 "Crispness" = 3 "Formant Preserving" = 0 } } ]
    inputs = [ "rb:Input L" "rb:Input R" ] outputs = [ "rb:Output L" "rb:Output R" ] }
  audio.channels = 2 audio.position = [ FL FR ]
  capture.props = { node.name = "jbl45" media.class = "Audio/Sink" audio.rate = 44100 }
  playback.props = { node.name = "jbl45.out" target.object = "jbl" node.passive = true audio.rate = 44100 } }'
timeout 40 pw-cli -m load-module libpipewire-module-filter-chain "$ARGS" > /tmp/pitch45.log 2>&1 &
sleep 2
STREAMS=$(pw-dump | python3 -c "
import json,sys
print(' '.join(str(o['id']) for o in json.load(sys.stdin) if ((o.get('info') or {}).get('props') or {}).get('node.name')=='spotify'))")
SINK=$(pw-dump | python3 -c "
import json,sys
print(next((str(o['id']) for o in json.load(sys.stdin) if ((o.get('info') or {}).get('props') or {}).get('node.name')=='jbl45'), ''))")
echo "streams=$STREAMS sink45=$SINK"
[ -n "$SINK" ] || { echo "no jbl45 sink"; tail -5 /tmp/pitch45.log; exit 1; }
for s in $STREAMS; do pw-metadata $s target.object jbl45 >/dev/null; done
echo "pitched for $SECONDS_PITCHED s"; sleep "$SECONDS_PITCHED"
for s in $STREAMS; do pw-metadata $s target.object jbl >/dev/null; done
sleep 1; pkill -f "pw-cli -m load-module libpipewire-module-filter-chain" || true
echo "back"; wpctl status | sed -n "/Streams:/,+6p" | head -8
