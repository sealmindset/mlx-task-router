# Quality Assurance System v2 — Guaranteed Routing Quality

## Overview

Upgrades the TBV system into a full quality assurance layer that **guarantees** local
responses match Claude quality. Three interlocking mechanisms work together:

1. **Confidence-Gated Routing** — Uncertain decisions are held for verification
2. **Pre-Delivery Shadow Validation** — Gated requests verified before user sees them
3. **Graduated Trust + Evidence Dashboard** — Statistical proof per request category

The end state: the router can prove, with statistical confidence, that every local
response is equivalent to what Claude would have produced. Any token spent on Claude
is demonstrably because the local model cannot handle it.

## Architecture

```
Request → classify() → forward_score
                            │
              ┌─────────────┼─────────────────────────┐
              │             │                          │
         score < 0.3    0.3 ≤ score < 0.7        score ≥ 0.7
        (HIGH confidence  (UNCERTAIN zone)       (HIGH confidence
         local/fast)        │                     forward)
              │             │                          │
              ▼             ▼                          ▼
         Deliver        GATE: Generate                Forward to
         immediately    local + shadow Claude         Claude
              │         in parallel                        │
              │             │                              │
              │         Claude judges:                     │
              │         equivalent? ──┐                    │
              │              │        │                    │
              │         YES: deliver  NO: deliver          │
              │         local         Claude's response    │
              │              │              │              │
              ▼              ▼              ▼              ▼
         ┌───────────────────────────────────────────────────┐
         │          Evidence Collector                        │
         │   (records category, confidence, outcome, scores) │
         │                                                   │
         │   Graduated Trust: once a category proves 95%+    │
         │   pass rate with 50+ samples, gate threshold      │
         │   narrows → more requests bypass the gate         │
         └───────────────────────────────────────────────────┘
                            │
                            ▼
         ┌───────────────────────────────────────────────────┐
         │          Evidence Dashboard                        │
         │   Per-category confidence intervals               │
         │   Real-time quality proof                         │
         │   Gate hit rate, shadow cost, quality trends      │
         └───────────────────────────────────────────────────┘
```

## 1. Confidence-Gated Routing

### Concept

Instead of binary LOCAL/FORWARD, introduce a **gray zone** where the router is uncertain.
Requests in this zone are held — both local and Claude generate in parallel, and Claude
validates quality before the response reaches the user.

### Gate Zones

| Zone | Forward Score Range | Action |
|------|-------------------|--------|
| Green (confident local) | `0.0 – gate_lower` | Deliver local immediately |
| Yellow (uncertain) | `gate_lower – gate_upper` | Shadow validate before delivery |
| Red (confident forward) | `gate_upper – 1.0` | Forward to Claude |

Default boundaries:
- `gate_lower` = 0.3 (below this, high confidence local is correct)
- `gate_upper` = 0.7 (above this, clearly needs Claude)
- These boundaries **narrow over time** as graduated trust grows

### Behavior in the Gate

1. Start local generation AND Claude generation in parallel
2. Wait for both to complete (with timeout)
3. Ask Claude (using the validation model) to judge equivalence
4. If equivalent → deliver local response (saves API cost on future similar requests)
5. If NOT equivalent → deliver Claude's response
6. Record the outcome for graduated trust

### Timeout Handling

- If local finishes but Claude hasn't in 5s → deliver local (user shouldn't wait)
- If Claude finishes but local hasn't → deliver Claude
- Record timeout events for dashboard

### Config

| Variable | Default | Description |
|----------|---------|-------------|
| `QA_GATE_ENABLED` | `false` | Enable confidence-gated routing |
| `QA_GATE_LOWER` | `0.3` | Below this score, deliver local immediately |
| `QA_GATE_UPPER` | `0.7` | Above this score, always forward to Claude |
| `QA_GATE_TIMEOUT` | `10` | Seconds to wait for shadow validation |
| `QA_GATE_VALIDATION_MODEL` | `claude-sonnet-4-20250514` | Model for equivalence judgments |

## 2. Pre-Delivery Shadow Validation

### Concept

For gated requests, run the **exact prompt** through both local model and Claude
simultaneously. Claude's response serves as the ground truth.

### Validation Prompt (sent to validation model)

```
You are validating whether a local AI model produced an equivalent response to a
frontier model for a coding task.

ORIGINAL REQUEST:
{request}

LOCAL MODEL RESPONSE:
{local_response}

FRONTIER MODEL RESPONSE:
{claude_response}

Are these responses functionally equivalent? Consider:
1. Would the user achieve the same outcome from either response?
2. Is the code correct in both?
3. Are there any errors or omissions in the local response that the frontier
   model correctly handles?

Return JSON:
{
  "equivalent": true/false,
  "confidence": 0.0-1.0,
  "reason": "brief explanation",
  "local_issues": ["list of problems in local response, if any"],
  "category": "categorize this request type (e.g. 'git_commands', 'code_generation', 'debugging', 'explanation')"
}
```

### Cost Analysis

- Gate zone requests: 2x generation cost (local + Claude) + 1 validation call
- But: as graduated trust narrows the gate, fewer requests hit this path
- Target: < 10% of requests go through the gate after warm-up

## 3. Graduated Trust with Evidence Dashboard

### Concept

Build statistical confidence per **request category**. Once a category proves reliable
(95%+ equivalence rate with 50+ samples), narrow the gate boundaries for that category
so more requests bypass validation.

### Categories (auto-detected)

The validation model assigns a category to each gated request. Example categories:
- `git_commands` — git status, git log, etc.
- `shell_commands` — ls, find, grep, etc.
- `simple_edits` — fix typo, rename variable
- `code_generation` — write a function, implement a class
- `debugging` — find the bug, explain error
- `explanation` — how does X work, what is Y
- `refactoring` — restructure, optimize
- `multi_file` — changes spanning multiple files
- `architecture` — system design, patterns

### Trust Levels Per Category

| Level | Criteria | Gate Behavior |
|-------|----------|---------------|
| **Unproven** | < 20 samples | Always gate (widest zone) |
| **Building** | 20-49 samples, ≥ 80% pass | Gate with standard zone |
| **Trusted** | 50+ samples, ≥ 95% pass | Narrow gate (±0.1 from threshold) |
| **Proven** | 100+ samples, ≥ 98% pass | Skip gate entirely (like confident local) |
| **Degraded** | Pass rate drops below 90% | Widen gate, increase sampling |

### Evidence Storage

```json
{
  "category": "git_commands",
  "trust_level": "proven",
  "total_samples": 156,
  "pass_count": 154,
  "pass_rate": 0.987,
  "confidence_interval_95": [0.953, 0.998],
  "last_failure": "2026-04-28T14:22:00Z",
  "gate_lower_override": 0.5,
  "gate_upper_override": 0.55,
  "recent_scores": [5, 5, 5, 4, 5, 5, 5, 5, 4, 5]
}
```

### Evidence Dashboard Requirements

The dashboard MUST show:

1. **Per-category trust table** — Category, trust level, sample count, pass rate,
   confidence interval, last failure time
2. **Quality trend chart** — Rolling 50-request pass rate over time, per category
3. **Gate hit rate** — What % of requests enter the gate vs bypass
4. **Cost impact** — Extra API cost from shadow validation vs savings from keeping local
5. **Real-time quality proof** — Current confidence that "local responses match Claude"
   expressed as a single percentage with confidence interval
6. **Failure drill-down** — Click any failure to see request, local response, Claude
   response, and validation judgment
7. **Self-annealing activity** — What adjustments have been made, when, why
8. **Gate boundary visualization** — Show current gate zone per category overlaid on
   the forward score distribution

### Overall Quality Score

A single number displayed prominently:

```
Quality Assurance: 97.3% [95% CI: 95.1% – 98.8%]
Based on 482 validated responses across 9 categories
```

This is the proof: "With 95% confidence, at least 95.1% of local responses are
equivalent to what Claude would produce."

## Config Summary

| Variable | Default | Description |
|----------|---------|-------------|
| `QA_GATE_ENABLED` | `false` | Master switch for confidence-gated routing |
| `QA_GATE_LOWER` | `0.3` | Green zone upper bound (confident local) |
| `QA_GATE_UPPER` | `0.7` | Red zone lower bound (confident forward) |
| `QA_GATE_TIMEOUT` | `10` | Max seconds for shadow validation |
| `QA_GATE_VALIDATION_MODEL` | `claude-sonnet-4-20250514` | Model for judging equivalence |
| `QA_TRUST_MIN_SAMPLES` | `50` | Samples needed for "Trusted" level |
| `QA_TRUST_PROVEN_SAMPLES` | `100` | Samples needed for "Proven" level |
| `QA_TRUST_PASS_THRESHOLD` | `0.95` | Pass rate for "Trusted" |
| `QA_TRUST_PROVEN_THRESHOLD` | `0.98` | Pass rate for "Proven" |

## Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/qa` | Overall QA status — quality score, confidence interval, gate stats |
| `GET` | `/qa/categories` | Per-category trust levels and evidence |
| `GET` | `/qa/evidence` | Recent validation results with full detail |
| `GET` | `/qa/failures` | Failed validations only, for debugging |
| `POST` | `/qa/enable` | Toggle QA gate: `{"enabled": bool}` |
| `POST` | `/qa/reset` | Clear all trust evidence and reset categories |
| `GET` | `/qa/cost` | Cost analysis — shadow validation spend vs savings |

## Files

| File | Purpose |
|------|---------|
| `src/mlx_task_router/qa_gate.py` | Confidence gate logic, parallel generation, shadow validation |
| `src/mlx_task_router/qa_trust.py` | Graduated trust, category tracking, evidence storage |
| `src/mlx_task_router/qa_dashboard.py` | Evidence dashboard HTML (separate from main dashboard) |
| `tests/test_qa_gate.py` | Gate logic tests |
| `tests/test_qa_trust.py` | Trust graduation tests |

## Implementation Order

1. Config additions (9 new env vars)
2. `qa_trust.py` — Category tracking, trust levels, evidence storage
3. `qa_gate.py` — Gate logic, parallel generation, validation calls
4. Wire into `server.py` — Replace simple LOCAL delivery with gated path
5. `qa_dashboard.py` — Evidence dashboard at `/qa/dashboard`
6. Endpoints — `/qa/*`
7. Tests
8. Main dashboard update + CHANGELOG

## Relationship to Existing TBV

The QA system subsumes TBV:
- TBV's async verification becomes the **post-delivery sampling** for green zone requests
- The gate provides **pre-delivery verification** for yellow zone requests
- TBV's verify_tuner continues operating, but now also receives gate outcomes
- The graduated trust system replaces TBV's simple pass rate with per-category evidence

Both can coexist: QA gate for uncertain requests, TBV sampling for confident ones.

## How This Answers "Does the Router Guarantee Quality?"

After warm-up (≈200-500 gated requests across categories):

1. **Proven categories** bypass the gate entirely — statistical evidence proves
   local handles them correctly ≥98% of the time
2. **Trusted categories** have a narrow gate — only borderline cases verified
3. **Uncertain requests** are always shadow-validated before delivery
4. **The dashboard** shows per-category confidence intervals, proving quality

The answer becomes: "Yes, with [X]% confidence based on [N] validated samples,
local responses are equivalent to Claude for [Y] out of [Z] request categories.
The remaining categories are gated — users always receive Claude-quality responses."
