#!/bin/bash
# One-time move of Demo Maker state out of the retired ~/voicemaker-studio
# checkout into ~/.alicia/state/demo-maker. Stops the old writer FIRST so the
# copy is the only live store, verifies counts, and refuses to overwrite.
set -euo pipefail
old="$HOME/voicemaker-studio"; new="$HOME/.alicia/state/demo-maker"
if [ -e "$new/studio.sqlite3" ]; then echo "refusing: $new already has a database"; exit 1; fi
launchctl bootout "gui/$(id -u)/com.jfstudio.voicemaker-studio" 2>/dev/null || true
sleep 2
if lsof -ti tcp:4173 -sTCP:LISTEN >/dev/null; then echo "port 4173 still served; old service running"; exit 1; fi
mkdir -p "$new/library"
sqlite3 "$old/server/db/studio.sqlite3" ".backup '$new/studio.sqlite3'"
cp -p "$old/.env" "$new/.env"
cp -Rp "$old/renders" "$new/renders"
cp -Rp "$old/public/library/videos" "$new/library/videos"
for t in library projects segments; do
  a=$(sqlite3 "$old/server/db/studio.sqlite3" "SELECT count(*) FROM $t"); b=$(sqlite3 "$new/studio.sqlite3" "SELECT count(*) FROM $t")
  if [ "$a" != "$b" ]; then echo "$t count mismatch $a != $b"; exit 1; fi; echo "$t: $b rows"
done
a=$(ls "$old/public/library/videos" | wc -l); b=$(ls "$new/library/videos" | wc -l)
if [ "$a" != "$b" ]; then echo "videos $a != $b"; exit 1; fi; echo "videos: $b files"
mv "$HOME/Library/LaunchAgents/com.jfstudio.voicemaker-studio.plist" "$new/retired-voicemaker-studio.plist"
