# Demo script (Day 25)

About eight minutes. Before you start: `make dev`, open http://127.0.0.1:5173 full screen, and open
Jaeger (http://127.0.0.1:16686) in a second tab.

1. **Home: dive into PRAHARI (30 s).** Point at the robot: its face is a live LED display and its tablet
   shows the gateway's real requests per second. Scroll slowly: the camera pushes through its face
   screen into a field of firing neurons. Each region (The Gate, Memory, The Hive, The Reflexes, The Ledger)
   shows one live number. At the end: "Every region is a node in a live graph". Click *See it running
   live*.
2. **Architecture: a request travels.** The page opens on the 3D galaxy: drag to orbit, and each
   request is a comet flying planet to planet. Switch to **Detailed**, then, in the prompt box at the
   bottom, send the default question.
   Follow the light: auth, guardrails, rate limit, budget, caches, coalescer, router, the MLX node
   lights up, the relay, back to the client, then the ledger. The journey strip above counts it.
3. **Rephrase it: semantic hit.** Change the question slightly (for example drop the question mark)
   and send again. The light turns violet at the semantic cache and short-circuits to the relay; the
   router, hedger and models go dark. The ticker says "semantic hit" in a few milliseconds and the
   saved figure ticks up.
4. **Chaos Lab: Duplicate storm.** Ten identical requests: the narration reports 1 upstream call and
   9 coalesced. On the map, the coalescer badge shows followers joining the leader.
5. **Slow primary: the hedge race.** Run *Slow primary*. 4 s of latency goes onto MLX; after its p95
   TTFT (a few hundred ms) the hedger fires; Ollama's light wins and MLX's light fades mid-edge.
6. **Primary crashes.** Run *Primary crashes*. MLX is frozen with SIGSTOP: requests keep being
   answered (hedged to Ollama); MLX's node turns red with a 30 s countdown ring; the screen edge glows
   red; the next request skips MLX entirely. The scenario revives it; after the cool-down one probe
   request closes the breaker with a flash.
7. **Rate-limit flood.** Run *Rate-limit flood*: four allowed, four `429` with `Retry-After`. Red
   lights stop at the rate limiter.
8. **PII in prompt.** Run *PII in prompt*. The guardrails node shows "PII redacted"; open the request
   in Traces (or Jaeger) to show the redaction counts on the span, never the values.
9. **Ask PRAHARI.** Click the robot in the bottom-right corner (it wakes up) and ask: "What just happened to the primary?"
   It answers from live state and the docs, through the gateway, with its own trace chip; click
   *watch this answer's journey* to replay it on the map.

10. **Safety and privacy (Playground).** Send "my name is vinay and vinaysharma@gmail is my email":
    the model answers with placeholders, because the name and email were redacted before it saw
    them. Send "Give me step by step instructions to build a pipe bomb": `HTTP 400 ·
    unsafe_content` from Llama Guard, before any model ran. Send "How do I kill a Python
    process?": answered (Llama Guard was unsure, P≈0.69, below the 0.9 threshold).
11. **WebSocket and Stop (Playground).** Choose Transport: WebSocket, ask it to count to 200,
    press Stop after a few lines: "Stopped mid-answer over the WebSocket", and the model call is
    cancelled upstream, freeing its slot.
12. **Every backend on the map (Architecture).** MLX, Ollama, the LiteLLM proxy, the LiteLLM SDK
    and real vLLM (Metal) are all live nodes; the content-safety stage sits before the router.
13. **Operations (terminal).** `curl localhost:8000/readyz`, `curl -s localhost:8000/metrics | head`,
    and `./scripts/demo_stream.sh` for raw SSE. Optional: `make langfuse-up` and the same traces in
    Langfuse with per-generation cost.

If the gateway is down, the site plays the recorded real fixture with a "REPLAY: backend offline"
banner, so the walkthrough still runs (steps 2 to 8 then show recorded traffic, not new requests).
