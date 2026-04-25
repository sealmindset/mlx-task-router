# MLX Task Router — TODO

## Completed

- [x] Fallback to cloud on local failure — if MLX model errors, automatically retry via Anthropic API
- [x] Always count tokens locally — use local model tokenizer instead of forwarding to Anthropic API

## Reliability

- [x] Health check watchdog — pings model every 30s, marks unhealthy after 3 failures, auto-recovers by reloading gear

## Routing Quality

- [x] Confidence scoring — score routing confidence instead of binary local/forward; only route locally above a threshold
- [x] Response quality feedback loop — tracks trigger success/failure rates, applies score penalty to unreliable triggers

## Cost Savings

- [x] Cache common responses — identical requests return cached results instantly (60s TTL, configurable)

## Observability

- [ ] Routing dashboard — web UI at `/dashboard` showing live stats, routing decisions, cost savings, gear status
- [ ] Per-session stats — track routing patterns per Claude Code session to identify optimization opportunities

## Performance

- [ ] Speculative local generation — start generating locally while checking if the request should be forwarded; cancel whichever loses
- [ ] Model prewarming — keep KV cache warm for common prompt prefixes
