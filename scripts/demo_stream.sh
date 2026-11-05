#!/usr/bin/env bash
# Demo helper: one streamed request through the gateway. Prints the gateway's response headers,
# then the answer token by token as the Server-Sent Events arrive.
Q="${1:-What is an AI gateway? Answer in two sentences.}"
curl -s -N -i http://127.0.0.1:8000/v1/chat/completions \
  -H 'authorization: Bearer prahari-demo-key' -H 'content-type: application/json' \
  -d "{\"model\":\"prahari-chat\",\"stream\":true,\"temperature\":0,\"messages\":[{\"role\":\"user\",\"content\":\"$Q\"}]}" |
python3 -u -c '
import json, sys
frames = 0
for line in sys.stdin:
    line = line.rstrip("\r\n")
    low = line.lower()
    if low.startswith(("content-type:", "x-cache:", "x-prahari-backend:", "x-prahari-trace-id:")):
        print(line)
    elif line.startswith("data: {"):
        if frames == 0:
            print("\nSSE stream:")
        frames += 1
        delta = json.loads(line[6:])["choices"][0]["delta"].get("content") or ""
        print(delta, end="", flush=True)
    elif line == "data: [DONE]":
        print(f"\n\n[DONE] after {frames} SSE frames")
'
