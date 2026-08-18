"""Check-digit driven MRZ error correction.

WHY THIS IS SUBTLE
------------------
A mod-10 check digit carries one decimal digit of information. Roughly one in
ten arbitrary substitutions therefore reproduces the same check digit by pure
coincidence. A naive "return the first candidate that validates" implementation
will confidently return a wrong value about as often as the right one once more
than a single character is corrupted.

That failure mode -- a wrong value flagged as verified -- is the worst outcome
this system can produce, so the algorithm here is built around three rules:

  1. Enumerate ALL candidates satisfying the check digit, not the first one.
  2. If several candidates tie at the minimum edit distance the field is
     AMBIGUOUS. Return the best guess but refuse to call it validated, and
     expose the alternatives so a human can choose.
  3. Use the composite check digit, which spans several fields at once, as a
     second independent constraint. It is what actually resolves most
     ambiguity, because a wrong repair in one field almost always breaks the
     composite even when that field's own check digit matches.

Rule 3 is why MRZ accuracy ends up high. Rule 2 is why the remaining errors
are visible instead of silent.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass, field

from packages.schema.validators import check_digit

# Characters that OCR confuses in the OCR-B font used by MRZ lines.
_CONFUSION_PAIRS = [
    ("0", "O"), ("0", "D"), ("0", "Q"), ("O", "D"), ("O", "Q"),
    ("1", "I"), ("1", "L"), ("I", "L"),
    ("2", "Z"), ("5", "S"), ("8", "B"), ("6", "G"), ("4", "A"),
    ("7", "T"), ("9", "G"), ("U", "V"), ("M", "N"), ("K", "X"),
    ("C", "G"), ("C", "O"), ("E", "F"), ("R", "P"), ("3", "8"),
]

CONFUSION: dict[str, set[str]] = {}
for _a, _b in _CONFUSION_PAIRS:
    CONFUSION.setdefault(_a, set()).add(_b)
    CONFUSION.setdefault(_b, set()).add(_a)

DIGITS = set("0123456789")
LETTERS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

# Charset constraints per field. These prune the search hard and stop the
# algorithm from "repairing" a date into something containing letters.
FIELD_CHARSETS: dict[str, set[str] | None] = {
    "doc_number": None,          # alphanumeric
    "birth_date": DIGITS,
    "expiry_date": DIGITS,
    "personal_number": None,
    "optional1": None,
    "nationality": LETTERS,
    "issuing_state": LETTERS,
}


@dataclass
class FieldCorrection:
    """Outcome of repairing one check-digit-protected field."""

    name: str
    original: str
    value: str                              # best candidate (or the original)
    valid: bool = False                     # check digit satisfied
    ambiguous: bool = False                 # several equally good candidates
    edits: int = 0
    corroborated: bool = False              # independent evidence for the edit
    candidates: list[str] = field(default_factory=list)
    positions: list[int] = field(default_factory=list)

    @property
    def trustworthy(self) -> bool:
        """Whether this value may be marked validated=True downstream.

        A check digit carries one decimal digit of information, so an EDITED
        value matching it is not proof -- roughly one in ten arbitrary edits
        matches by coincidence. Editing until the check digit agrees and then
        declaring success is how OCR pipelines produce confidently wrong
        passport numbers.

        So only two situations earn trust:
          * edits == 0 -- the value as actually read already satisfies its
            check digit. Nothing was invented.
          * the edit was CORROBORATED by independent evidence: either the
            recogniser itself flagged those exact positions as uncertain, or
            the composite check digit (which spans several fields) confirms
            this particular combination.

        Everything else is a suggestion for a human, not a verified fact.
        """
        if not self.valid or self.ambiguous:
            return False
        return self.edits == 0 or self.corroborated

    @property
    def changed(self) -> bool:
        return self.value != self.original

    def __str__(self) -> str:
        if not self.changed:
            return f"{self.name}: unchanged ({'valid' if self.valid else 'invalid'})"
        tag = "ambiguous" if self.ambiguous else "repaired"
        return f"{self.name}: {self.original!r} -> {self.value!r} [{tag}]"


CorrectionLog = FieldCorrection  # backwards-compatible alias


def _alternatives_for(ch: str, allowed: set[str] | None) -> list[str]:
    """Substitution options for one character, respecting a charset."""
    opts = {ch} | CONFUSION.get(ch, set())
    if allowed is not None:
        filtered = {c for c in opts if c in allowed}
        opts = filtered or {ch}
    return sorted(opts)


def find_candidates(
    raw: str,
    expected_cd: str,
    *,
    charset: set[str] | None = None,
    max_edits: int = 3,
    max_evaluated: int = 20000,
) -> list[tuple[str, int, tuple[int, ...]]]:
    """All strings within `max_edits` substitutions satisfying the check digit.

    Returns (candidate, edit_count, changed_positions) sorted by edit count.
    Search stops widening as soon as any candidate is found: a three-edit
    explanation is never preferable to a one-edit explanation.
    """
    if not expected_cd.isdigit():
        return []
    target = int(expected_cd)

    results: list[tuple[str, int, tuple[int, ...]]] = []
    if check_digit(raw) == target:
        return [(raw, 0, ())]

    mutable = [
        i for i, ch in enumerate(raw)
        if any(c != ch for c in _alternatives_for(ch, charset))
    ]
    evaluated = 0

    for k in range(1, min(max_edits, len(mutable)) + 1):
        for combo in itertools.combinations(mutable, k):
            choices = [
                [c for c in _alternatives_for(raw[i], charset) if c != raw[i]]
                for i in combo
            ]
            for repl in itertools.product(*choices):
                evaluated += 1
                if evaluated > max_evaluated:
                    return sorted(results, key=lambda r: r[1])
                chars = list(raw)
                for pos, c in zip(combo, repl, strict=True):
                    chars[pos] = c
                cand = "".join(chars)
                if check_digit(cand) == target:
                    results.append((cand, k, combo))
        if results:
            break

    return sorted(results, key=lambda r: r[1])


def correct_field(
    raw: str,
    expected_cd: str,
    *,
    name: str = "",
    charset: set[str] | None = None,
    confidences: list[float] | None = None,
    max_edits: int = 3,
) -> FieldCorrection:
    """Repair `raw` so its check digit equals `expected_cd`.

    When several candidates tie at the minimum edit distance the result is
    marked ambiguous. Per-character OCR confidence, when supplied, breaks such
    ties: a repair at a position the recogniser was already unsure about is far
    more plausible than one at a position it was confident about.
    """
    cands = find_candidates(raw, expected_cd, charset=charset, max_edits=max_edits)

    if not cands:
        return FieldCorrection(name=name, original=raw, value=raw, valid=False)

    best_edits = cands[0][1]
    tied = [c for c in cands if c[1] == best_edits]

    if best_edits == 0:
        return FieldCorrection(name=name, original=raw, value=raw, valid=True,
                               edits=0, candidates=[raw])

    if len(tied) > 1 and confidences and len(confidences) == len(raw):
        def cost(item: tuple[str, int, tuple[int, ...]]) -> float:
            return sum(confidences[p] for p in item[2])

        tied.sort(key=cost)
        if cost(tied[0]) < cost(tied[1]) - 1e-9:
            chosen = tied[0]
            # The recogniser independently flagged these positions as weak,
            # so the repair has evidence beyond the check digit itself.
            low_conf = all(confidences[p] < 0.5 for p in chosen[2])
            return FieldCorrection(
                name=name, original=raw, value=chosen[0], valid=True,
                ambiguous=False, edits=chosen[1], corroborated=low_conf,
                candidates=[c[0] for c in tied[:10]], positions=list(chosen[2]),
            )

    chosen = tied[0]
    corroborated = bool(
        confidences
        and len(confidences) == len(raw)
        and all(confidences[p] < 0.5 for p in chosen[2])
    )
    return FieldCorrection(
        name=name, original=raw, value=chosen[0], valid=True,
        ambiguous=len(tied) > 1, edits=chosen[1], corroborated=corroborated,
        candidates=[c[0] for c in tied[:10]], positions=list(chosen[2]),
    )


def disambiguate_with_composite(
    corrections: dict[str, FieldCorrection],
    build_composite: Callable[[dict[str, str]], tuple[str, str]],
    max_combinations: int = 5000,
) -> dict[str, FieldCorrection]:
    """Resolve ambiguity using the composite check digit.

    The composite spans several fields simultaneously, giving an independent
    constraint. If exactly one combination of per-field candidates satisfies
    it, the ambiguity is genuinely resolved and those fields become
    trustworthy. If zero or several do, the fields stay ambiguous -- which is
    the honest answer, not a failure.

    Args:
        corrections: per-field results from `correct_field`.
        build_composite: given {field: value}, returns
            (composite_source_string, expected_check_digit).
    """
    ambiguous = {k: c for k, c in corrections.items() if c.ambiguous}
    if not ambiguous:
        return corrections

    names = list(ambiguous)
    option_lists = [ambiguous[n].candidates or [ambiguous[n].value] for n in names]

    total = 1
    for opts in option_lists:
        total *= len(opts)
    if total > max_combinations:
        return corrections

    fixed = {k: c.value for k, c in corrections.items() if k not in ambiguous}
    solutions: list[dict[str, str]] = []

    for combo in itertools.product(*option_lists):
        trial = dict(fixed)
        trial.update(dict(zip(names, combo, strict=True)))
        try:
            source, expected = build_composite(trial)
        except Exception:
            return corrections
        if expected.isdigit() and check_digit(source) == int(expected):
            solutions.append(trial)
            if len(solutions) > 1:
                return corrections          # still ambiguous; say so

    if len(solutions) != 1:
        return corrections

    winner = solutions[0]
    out = dict(corrections)
    for n in names:
        c = ambiguous[n]
        out[n] = FieldCorrection(
            name=c.name, original=c.original, value=winner[n],
            valid=True, ambiguous=False, edits=c.edits,
            corroborated=True,          # the composite is independent evidence
            candidates=c.candidates, positions=c.positions,
        )
    return out


def correct_mrz_fields(
    fields: dict[str, tuple[str, str]],
    confidences: dict[str, list[float]] | None = None,
) -> tuple[dict[str, FieldCorrection], list[FieldCorrection]]:
    """Correct {name: (raw_value, check_digit)} pairs.

    Returns the full correction map plus the subset that changed or is
    ambiguous, for the audit log.
    """
    confidences = confidences or {}
    out: dict[str, FieldCorrection] = {}
    for name, (raw, cd) in fields.items():
        out[name] = correct_field(
            raw, cd, name=name,
            charset=FIELD_CHARSETS.get(name),
            confidences=confidences.get(name),
        )
    changed = [c for c in out.values() if c.changed or c.ambiguous]
    return out, changed


def normalize_line(line: str, width: int) -> str:
    """Force an OCR'd MRZ line to the exact expected width and alphabet."""
    s = line.strip().upper().replace(" ", "<")
    s = "".join(ch for ch in s if ch in DIGITS | LETTERS | {"<"})
    if len(s) < width:
        s = s.ljust(width, "<")
    return s[:width]
