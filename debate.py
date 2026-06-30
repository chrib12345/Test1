"""
Multi-LLM debate-to-consensus.

Several LLM "debaters" (each with its own persona, and optionally its own model)
argue a topic over multiple rounds. After every round a "judge" model decides
whether the debaters have converged. Once consensus is reached — or a round cap
is hit — a "moderator" model synthesizes the final answer.

Usage:
    export ANTHROPIC_API_KEY=...        # or run `ant auth login`
    python debate.py "Should self-driving cars be allowed on public roads?"
    python debate.py "Is remote work better than office work?" --rounds 6
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

import anthropic
from pydantic import BaseModel, Field

client = anthropic.Anthropic()

# Default to the most capable current model. Mixing models (e.g. one debater on
# opus, one on sonnet, one on haiku) genuinely makes them "multiple LLMs" and
# tends to surface more diverse arguments.
DEFAULT_MODEL = "claude-opus-4-8"


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
@dataclass
class Debater:
    name: str
    persona: str
    model: str = DEFAULT_MODEL


# A reasonable default panel. Edit freely — add debaters, change models/personas.
DEFAULT_PANEL: list[Debater] = [
    Debater(
        name="Ada",
        persona=(
            "a rigorous, evidence-first analyst who prizes data, measurable "
            "outcomes, and intellectual honesty. You change your mind when the "
            "evidence warrants it."
        ),
        model="claude-opus-4-8",
    ),
    Debater(
        name="Boyd",
        persona=(
            "a pragmatic skeptic who stress-tests claims, surfaces trade-offs, "
            "second-order effects, and edge cases others overlook. You are "
            "fair-minded, not contrarian for its own sake."
        ),
        model="claude-sonnet-4-6",
    ),
    Debater(
        name="Cleo",
        persona=(
            "a values-and-stakeholders thinker who weighs ethics, fairness, and "
            "human impact, and seeks common ground without papering over real "
            "disagreement."
        ),
        model="claude-haiku-4-5",
    ),
]


# --------------------------------------------------------------------------- #
# Judge output schema (structured output → no parsing guesswork)
# --------------------------------------------------------------------------- #
class Verdict(BaseModel):
    consensus_reached: bool = Field(
        description="True only if the debaters have substantively converged."
    )
    agreement_score: float = Field(
        description="Estimated agreement from 0.0 (total disagreement) to 1.0 (full consensus)."
    )
    points_of_agreement: list[str]
    points_of_disagreement: list[str]
    reasoning: str = Field(description="Brief justification for the verdict.")


# --------------------------------------------------------------------------- #
# Core steps
# --------------------------------------------------------------------------- #
def render_transcript(transcript: list[tuple[str, str]]) -> str:
    if not transcript:
        return "(no arguments yet — this is the opening round)"
    return "\n\n".join(f"### {name}\n{text}" for name, text in transcript)


def debater_turn(
    debater: Debater, topic: str, transcript: list[tuple[str, str]], round_num: int
) -> str:
    """One debater's contribution for the current round."""
    system = (
        f"You are {debater.name}, {debater.persona}\n\n"
        "You are one participant in a multi-party debate whose goal is to reach a "
        "well-reasoned CONSENSUS — not to 'win'. Engage directly with what others "
        "have said: concede strong points, push back on weak ones, and move the "
        "group toward shared conclusions. Be concise (a few tight paragraphs). "
        "Do not roleplay the other debaters or invent quotes."
    )
    user = (
        f"TOPIC: {topic}\n\n"
        f"DEBATE SO FAR (round {round_num}):\n{render_transcript(transcript)}\n\n"
        f"Give {debater.name}'s contribution for this round."
    )
    resp = client.messages.create(
        model=debater.model,
        max_tokens=1500,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return next((b.text for b in resp.content if b.type == "text"), "").strip()


def judge_round(topic: str, transcript: list[tuple[str, str]]) -> Verdict:
    """Decide whether the debaters have reached consensus."""
    user = (
        f"TOPIC: {topic}\n\n"
        f"FULL DEBATE TRANSCRIPT:\n{render_transcript(transcript)}\n\n"
        "Assess whether the debaters have reached a substantive consensus. "
        "Be strict: superficial politeness or partial overlap is NOT consensus. "
        "Consensus means they broadly agree on the core conclusion, even if minor "
        "caveats remain."
    )
    resp = client.messages.parse(
        model=DEFAULT_MODEL,
        max_tokens=1500,
        system="You are a neutral, exacting judge of debates.",
        messages=[{"role": "user", "content": user}],
        output_format=Verdict,
    )
    return resp.parsed_output


def synthesize_final(
    topic: str, transcript: list[tuple[str, str]], verdict: Verdict, converged: bool
) -> str:
    """Moderator produces the final answer."""
    status = (
        "The debaters reached consensus."
        if converged
        else "The debaters did NOT fully converge within the round limit."
    )
    user = (
        f"TOPIC: {topic}\n\n"
        f"FULL DEBATE TRANSCRIPT:\n{render_transcript(transcript)}\n\n"
        f"OUTCOME: {status}\n"
        f"Judge's final agreement score: {verdict.agreement_score:.2f}\n\n"
        "As the moderator, write the final output: a clear, balanced answer to the "
        "topic that reflects where the debaters landed. State the shared conclusion, "
        "note any unresolved disagreements honestly, and keep it readable."
    )
    resp = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=2000,
        system="You are an impartial moderator synthesizing a debate into a final answer.",
        messages=[{"role": "user", "content": user}],
    )
    return next((b.text for b in resp.content if b.type == "text"), "").strip()


# --------------------------------------------------------------------------- #
# Orchestration loop
# --------------------------------------------------------------------------- #
def run_debate(
    topic: str,
    panel: list[Debater] = DEFAULT_PANEL,
    max_rounds: int = 4,
    consensus_threshold: float = 0.85,
) -> str:
    transcript: list[tuple[str, str]] = []
    verdict = Verdict(
        consensus_reached=False,
        agreement_score=0.0,
        points_of_agreement=[],
        points_of_disagreement=[],
        reasoning="Debate not yet started.",
    )
    converged = False

    print(f"\n{'=' * 70}\nTOPIC: {topic}\n{'=' * 70}")

    for round_num in range(1, max_rounds + 1):
        print(f"\n----- Round {round_num} -----")
        for debater in panel:
            text = debater_turn(debater, topic, transcript, round_num)
            transcript.append((f"{debater.name} ({debater.model})", text))
            print(f"\n[{debater.name} / {debater.model}]\n{text}")

        verdict = judge_round(topic, transcript)
        print(
            f"\n[JUDGE] agreement={verdict.agreement_score:.2f} "
            f"consensus={verdict.consensus_reached} — {verdict.reasoning}"
        )

        if verdict.consensus_reached or verdict.agreement_score >= consensus_threshold:
            converged = True
            print(f"\n>>> Consensus reached after round {round_num}.")
            break
    else:
        print(f"\n>>> No consensus after {max_rounds} rounds.")

    final = synthesize_final(topic, transcript, verdict, converged)
    print(f"\n{'=' * 70}\nFINAL OUTPUT\n{'=' * 70}\n{final}\n")
    return final


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-LLM debate to consensus.")
    parser.add_argument("topic", help="The topic or question to debate.")
    parser.add_argument(
        "--rounds", type=int, default=4, help="Max debate rounds (default: 4)."
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.85,
        help="Agreement score (0-1) that counts as consensus (default: 0.85).",
    )
    args = parser.parse_args()

    try:
        run_debate(args.topic, max_rounds=args.rounds, consensus_threshold=args.threshold)
    except anthropic.APIError as e:
        print(f"API error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
