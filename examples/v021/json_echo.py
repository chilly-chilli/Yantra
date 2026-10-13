"""Read-only JSON command example: one input object, one output object."""
import json
import sys

arguments = json.load(sys.stdin)
print(json.dumps({"text": arguments["text"]}, ensure_ascii=False))
