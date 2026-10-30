"""Stands in for `claude` in the end-to-end tests, so they need no login and use no quota.
It really reads stdin and answers in the CLI's JSON shape. The behaviour comes from a file, so a test can
change it while the app is running."""
import json
import os
import re
import sys
import time

mode_file = os.environ.get("FAKE_CLAUDE_MODE_FILE", "")
behaviour = "ok"
if mode_file and os.path.exists(mode_file):
    behaviour = open(mode_file, encoding="utf-8").read().strip() or "ok"

FIXES = [("as nezinau", "Aš nežinau"), ("ka darit", "ką daryti"), ("parduotuve", "parduotuvę"),
         ("nupirksiu pieno", "nupirksiu pieno"), ("gerai bus", "Gerai bus"), ("sveikas", "Sveikas"), ("teh", "the")]


def emit(payload, code=0):
    sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    sys.stdout.flush()
    sys.exit(code)


if len(sys.argv) > 2 and sys.argv[1:3] == ["auth", "status"]:
    emit({"loggedIn": behaviour != "notlogged", "authMethod": "claude.ai", "subscriptionType": "pro"})

count_file = os.environ.get("FAKE_CLAUDE_COUNT_FILE")
if count_file:
    with open(count_file, "a", encoding="utf-8") as f:
        f.write(" ".join(a for a in sys.argv[1:] if a.startswith("--")) + "\n")

stdin = sys.stdin.buffer.read().decode("utf-8")
match = re.search(r"<text>\n(.*)\n</text>", stdin, re.S)
text = match.group(1) if match else ""
for wrong, right in FIXES:
    text = text.replace(wrong, right)

if behaviour.startswith("slow"):
    time.sleep(float(behaviour[4:] or 3))
elif behaviour == "notlogged":
    emit({"type": "result", "is_error": True, "result": "Not logged in · Please run /login"}, 1)
elif behaviour == "expired":
    emit({"type": "result", "is_error": True,
          "result": "Failed to authenticate: OAuth session expired and could not be refreshed"}, 1)
elif behaviour == "limit":
    emit({"type": "result", "is_error": True, "result": "You've hit your session limit · resets 7pm"}, 1)
elif behaviour == "hang":
    time.sleep(600)
elif behaviour == "empty":
    text = ""
elif behaviour == "huge":
    text = text * 30
elif behaviour == "commentary":
    emit({"type": "result", "is_error": False, "result": "Here is the corrected text:\n\n```\n" + text + "\n```"})

emit({"type": "result", "is_error": False, "result": "", "structured_output": {"corrected": text},
      "duration_api_ms": 1200, "modelUsage": {"fake-model": {}}})
