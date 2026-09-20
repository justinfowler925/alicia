#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
STUDIO_TARGET=${BRUTUS_STUDIO_SSH:-jfstudio@100.102.92.119}
REMOTE_DIR=/Users/jfstudio/.local/share/workflow-efficiency
REMOTE_PLIST=/Users/jfstudio/Library/LaunchAgents/com.jfstudio.weekly-workflow-efficiency.plist
LABEL=com.jfstudio.weekly-workflow-efficiency

/usr/bin/plutil -lint "$ROOT/scripts/launchd/$LABEL.plist"
ssh -o BatchMode=yes "$STUDIO_TARGET" "mkdir -p '$REMOTE_DIR/reports' /Users/jfstudio/Library/LaunchAgents"
scp -q "$ROOT/scripts/weekly_work_recap.py" "$STUDIO_TARGET:$REMOTE_DIR/weekly_work_recap.py"
scp -q "$ROOT/scripts/weekly-workflow-efficiency.py" "$STUDIO_TARGET:$REMOTE_DIR/weekly-workflow-efficiency.py"
scp -q "$ROOT/scripts/launchd/com.jfstudio.weekly-work-recap-page.plist" "$STUDIO_TARGET:/Users/jfstudio/Library/LaunchAgents/com.jfstudio.weekly-work-recap-page.plist"
scp -q "$ROOT/scripts/launchd/$LABEL.plist" "$STUDIO_TARGET:$REMOTE_PLIST"
ssh -o BatchMode=yes "$STUDIO_TARGET" "
  set -e
  export PATH=/opt/homebrew/bin:$PATH
  test -x /opt/homebrew/bin/python3.12
  chmod 755 '$REMOTE_DIR/weekly-workflow-efficiency.py'
  /opt/homebrew/bin/python3.12 '$REMOTE_DIR/weekly-workflow-efficiency.py'
  test -s '$REMOTE_DIR/reports/'\$(date +%F)'.json'
  launchctl bootout 'gui/501/$LABEL' 2>/dev/null || true
  launchctl bootstrap gui/501 '$REMOTE_PLIST'
  launchctl print 'gui/501/$LABEL'
  launchctl bootout gui/501/com.jfstudio.weekly-work-recap-page 2>/dev/null || true
  launchctl bootstrap gui/501 /Users/jfstudio/Library/LaunchAgents/com.jfstudio.weekly-work-recap-page.plist
"
