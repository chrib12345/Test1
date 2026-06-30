# Multi-LLM Debate → Consensus

A small program that has several LLM "debaters" argue a topic over multiple
rounds and produce a final answer once they reach **consensus** (or a round
limit is hit).

## How it works

```
            ┌─────────────────────────────────────────────┐
            │  Round loop (up to --rounds)                 │
topic ───▶  │   each debater argues, seeing the full       │
            │   transcript so far  ──▶  judge scores        │
            │   agreement  ──▶  consensus? ──┐              │
            └───────────────────────────────│──────────────┘
                                            yes / round cap
                                             │
                                             ▼
                                   moderator synthesizes
                                     the FINAL OUTPUT
```

1. **Debaters** — each is an LLM with its own persona, and optionally its own
   model. Mixing models (Opus / Sonnet / Haiku) makes them genuinely "multiple
   LLMs" and surfaces more diverse arguments.
2. **Judge** — after each round, a model returns a structured verdict
   (`consensus_reached`, `agreement_score`, points of agreement/disagreement).
   Using structured output means no fragile text parsing.
3. **Moderator** — once consensus is detected (or `--rounds` is exhausted),
   synthesizes the debate into a single, balanced final answer.

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...   # or run `ant auth login`
```

## Usage

```bash
python debate.py "Should self-driving cars be allowed on public roads?"
python debate.py "Is remote work better than office work?" --rounds 6 --threshold 0.9
```

## Customizing

- **The panel** — edit `DEFAULT_PANEL` in `debate.py` to add/remove debaters or
  change their personas and models.
- **Consensus strictness** — `--threshold` (0–1) is the agreement score that
  counts as consensus; the judge can also flag consensus directly.
- **Length** — `--rounds` caps the debate; the moderator always produces a final
  answer even if the panel never fully converges (it says so honestly).

## Notes

- This calls the Anthropic API once per debater per round, plus one judge call
  per round and one final synthesis call — keep an eye on token usage for large
  panels / many rounds.
- The same pattern works with any LLM provider; this implementation uses the
  Anthropic SDK and `claude-opus-4-8` by default.
