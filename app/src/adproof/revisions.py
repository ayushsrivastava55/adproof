"""Draft revision instructions for a creator.

USER_JOURNEYS.md Journey 4: "System drafts revision instructions grounded in
the rule and evidence", with the acceptance criterion that instructions
**must not claim facts unsupported by evidence**.

That criterion drives the whole design here:

  * a measured shortfall may state the measurement, because it was measured;
  * an ABSENCE may not say "you didn't do it" -- we only know we could not find
    it, so the wording asks the creator to point us at it;
  * an uncertain result asks for confirmation instead of asserting a fault;
  * a processing error or unevaluated rule produces NO instruction at all,
    because we did not look and have nothing to ask for.

Pure functions. No I/O, no language model. Every sentence is assembled from
recorded values, so the draft cannot invent a fact. The reviewer edits and
approves before anything is sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .states import AbsenceClass, RuleResultState, RuleType, Severity


@dataclass(frozen=True)
class EvidenceRef:
    start_seconds: float
    end_seconds: float | None


@dataclass(frozen=True)
class RuleFacts:
    """Everything the drafter is allowed to know about one rule."""

    rule_id: str
    rule_type: RuleType
    requirement_text: str
    severity: Severity
    state: RuleResultState
    absence_class: AbsenceClass
    measured_value: float | None
    measured_unit: str | None
    threshold_value: float | None
    measurement_resolution_seconds: float | None = None
    #: Only evidence that counted toward the measurement.
    evidence: list[EvidenceRef] = field(default_factory=list)
    phrase: str | None = None
    visual_concept: str | None = None


@dataclass(frozen=True)
class RevisionItem:
    rule_id: str
    requirement_text: str
    severity: Severity
    #: What the creator is being asked to do.
    instruction: str
    #: Why, stated only from recorded facts.
    basis: str
    #: True when we are asking the creator to point us at something rather than
    #: asserting it is missing.
    is_query_not_assertion: bool


@dataclass(frozen=True)
class RevisionDraft:
    items: list[RevisionItem]
    #: Rules deliberately excluded, with the reason. Surfaced so a reviewer can
    #: see what was left out rather than assuming the draft is exhaustive.
    excluded: list[tuple[str, str]] = field(default_factory=list)

    @property
    def message(self) -> str:
        """A plain-text draft the reviewer can edit and send."""
        if not self.items:
            return (
                "No revision instructions could be drafted from the current "
                "results."
            )
        lines = [
            "Thanks for the submission. Before we can approve it, please look "
            "at the following:",
            "",
        ]
        for index, item in enumerate(self.items, start=1):
            lines.append(f"{index}. {item.instruction}")
            lines.append(f"   ({item.basis})")
            lines.append("")
        lines.append(
            "If you think any of these are already satisfied, reply with the "
            "timestamp and we will re-check."
        )
        return "\n".join(lines).strip()


def _fmt_time(seconds: float) -> str:
    minutes, secs = divmod(seconds, 60)
    return f"{int(minutes)}:{secs:05.2f}"


def _window(evidence: list[EvidenceRef]) -> str:
    """Describe where the evidence actually is, using real timestamps only."""
    if not evidence:
        return ""
    spans = [
        f"{_fmt_time(e.start_seconds)}"
        + (f"–{_fmt_time(e.end_seconds)}" if e.end_seconds is not None else "")
        for e in evidence[:3]
    ]
    more = "" if len(evidence) <= 3 else f", and {len(evidence) - 3} more"
    return ", ".join(spans) + more


def draft_revisions(rules: list[RuleFacts]) -> RevisionDraft:
    """Build revision instructions for the rules a creator can act on."""
    items: list[RevisionItem] = []
    excluded: list[tuple[str, str]] = []

    for rule in rules:
        # States we must not write instructions for: we either did not look, or
        # the result already satisfies the requirement.
        if rule.state is RuleResultState.passed:
            continue
        if rule.state in (RuleResultState.processing, RuleResultState.not_evaluated):
            excluded.append(
                (rule.rule_id, "Not evaluated yet; nothing can be asked for.")
            )
            continue
        if rule.state is RuleResultState.error:
            excluded.append(
                (
                    rule.rule_id,
                    "Analysis failed for this requirement. Re-run processing "
                    "before asking the creator for anything.",
                )
            )
            continue
        if rule.absence_class is AbsenceClass.provider_failure:
            excluded.append(
                (rule.rule_id, "Retrieval failed; the media was never searched.")
            )
            continue
        if rule.state is RuleResultState.human_review_required:
            excluded.append(
                (
                    rule.rule_id,
                    "Requires human judgement; not a mechanical fix for the "
                    "creator.",
                )
            )
            continue

        item = _item_for(rule)
        if item is not None:
            items.append(item)

    # Blocking problems first: they are what stops approval.
    order = {Severity.blocking: 0, Severity.required: 1, Severity.optional: 2}
    items.sort(key=lambda i: order[i.severity])
    return RevisionDraft(items=items, excluded=excluded)


def _item_for(rule: RuleFacts) -> RevisionItem | None:
    # "We found nothing" must be true in BOTH senses: no evidence rows AND no
    # non-zero measurement. A measurement without evidence should never occur,
    # but if it does, claiming we found nothing would contradict our own number.
    found_nothing = not rule.evidence and not rule.measured_value

    # --- absence: ask, never accuse ---------------------------------------
    if found_nothing:
        subject = (
            f'the phrase "{rule.phrase}"'
            if rule.rule_type is RuleType.required_spoken_phrase
            else f"{rule.visual_concept}"
        )
        where = (
            "in the spoken audio"
            if rule.rule_type is RuleType.required_spoken_phrase
            else "on screen"
        )
        return RevisionItem(
            rule_id=rule.rule_id,
            requirement_text=rule.requirement_text,
            severity=rule.severity,
            instruction=(
                f"Please make sure {subject} is clearly present {where} — or, "
                f"if it already is, reply with the timestamp so we can re-check."
            ),
            # Deliberately hedged: an empty search is not proof of absence.
            basis=(
                f"Requirement: {rule.requirement_text}. We searched and did not "
                f"find it, which is not the same as it being absent — our "
                f"detection can miss things."
            ),
            is_query_not_assertion=True,
        )

    # --- uncertain with evidence: ask for confirmation --------------------
    if rule.state is RuleResultState.uncertain:
        measured = (
            f"about {rule.measured_value:g} {rule.measured_unit}"
            if rule.measured_value is not None
            else "an amount we could not measure reliably"
        )
        return RevisionItem(
            rule_id=rule.rule_id,
            requirement_text=rule.requirement_text,
            severity=rule.severity,
            instruction=(
                f"Please confirm or strengthen this: {rule.requirement_text}. "
                f"Making it more prominent would remove the ambiguity."
            ),
            basis=(
                f"We measured {measured} against a threshold of "
                f"{rule.threshold_value:g} {rule.measured_unit or ''}, at "
                f"{_window(rule.evidence)}. That is too close to call with "
                f"confidence."
            ),
            is_query_not_assertion=True,
        )

    # --- measured shortfall: state the measurement ------------------------
    if rule.state is RuleResultState.failed and rule.measured_value is not None:
        if rule.rule_type is RuleType.min_visual_duration:
            shortfall = (rule.threshold_value or 0) - rule.measured_value
            resolution = (
                f" (measured to about ±{rule.measurement_resolution_seconds:g}s)"
                if rule.measurement_resolution_seconds
                else ""
            )
            return RevisionItem(
                rule_id=rule.rule_id,
                requirement_text=rule.requirement_text,
                severity=rule.severity,
                instruction=(
                    f"Show {rule.visual_concept} clearly for at least "
                    f"{rule.threshold_value:g} seconds — roughly "
                    f"{shortfall:.1f}s more than the current cut."
                ),
                basis=(
                    f"We measured {rule.measured_value:.1f}s of clear visibility"
                    f"{resolution}, at {_window(rule.evidence)}."
                ),
                is_query_not_assertion=False,
            )

        if rule.rule_type is RuleType.required_spoken_phrase:
            return RevisionItem(
                rule_id=rule.rule_id,
                requirement_text=rule.requirement_text,
                severity=rule.severity,
                instruction=(
                    f'Say "{rule.phrase}" at least '
                    f"{rule.threshold_value:g} time(s) — we heard it "
                    f"{rule.measured_value:g} time(s)."
                ),
                basis=f"Heard at {_window(rule.evidence)}.",
                is_query_not_assertion=False,
            )

    # Anything else: fall back to restating the requirement without inventing
    # a diagnosis.
    return RevisionItem(
        rule_id=rule.rule_id,
        requirement_text=rule.requirement_text,
        severity=rule.severity,
        instruction=f"Please address this requirement: {rule.requirement_text}.",
        basis="This requirement did not pass. See the linked evidence.",
        is_query_not_assertion=True,
    )
