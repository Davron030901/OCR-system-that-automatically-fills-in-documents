"""Tests for MRZ correction and parsing.

The correction tests are the ones that matter most: they quantify the claim
that check digits let us repair OCR errors rather than merely detect them.
"""

import random

from packages.ml.mrz.correct import CONFUSION, correct_field, normalize_line
from packages.ml.mrz.parse import parse_mrz, parse_td1, parse_td3
from packages.schema.validators import check_digit

ICAO_TD3 = [
    "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<",
    "L898902C36UTO7408122F1204159ZE184226B<<<<<10",
]
ICAO_TD1 = [
    "I<UTOD231458907<<<<<<<<<<<<<<<",
    "7408122F1204159UTO<<<<<<<<<<<6",
    "ERIKSSON<<ANNA<MARIA<<<<<<<<<<",
]


class TestNormalizeLine:
    def test_pads_short_line(self):
        assert normalize_line("ABC", 10) == "ABC<<<<<<<"

    def test_trims_long_line(self):
        assert normalize_line("A" * 50, 44) == "A" * 44

    def test_spaces_become_filler(self):
        assert normalize_line("AB CD", 5) == "AB<CD"

    def test_strips_illegal_characters(self):
        assert normalize_line("AB!@#CD", 5) == "ABCD<"


class TestCorrectField:
    def test_already_valid_untouched(self):
        c = correct_field("D23145890", "7")
        assert c.valid and c.value == "D23145890" and not c.changed

    def test_repairs_single_ocr_error(self):
        c = correct_field("D2314589O", "7")
        assert c.valid and c.value == "D23145890"

    def test_charset_prevents_letters_in_dates(self):
        c = correct_field("74O812", "2", charset=set("0123456789"))
        assert c.valid
        assert c.value == "740812"
        assert c.value.isdigit()

    def test_non_digit_check_digit_gives_up(self):
        c = correct_field("D23145890", "<")
        assert not c.valid

    def test_confusion_table_is_bidirectional(self):
        for a, bs in CONFUSION.items():
            for b in bs:
                assert a in CONFUSION[b], f"{a}<->{b} not symmetric"

    def test_uncorroborated_edit_is_never_trustworthy(self):
        """An edit that only the check digit supports is a suggestion."""
        c = correct_field("L89890ZC3", "6")
        assert c.valid
        assert c.edits > 0
        assert not c.trustworthy, "an unsupported edit must not be validated"

    def test_ambiguity_is_reported_not_hidden(self):
        c = correct_field("L89890ZC3", "6")
        if len(c.candidates) > 1:
            assert c.ambiguous
            assert not c.trustworthy

    def test_confidence_breaks_ties(self):
        """A repair at a low-confidence position beats a high-confidence one."""
        raw = "L89890ZC3"
        conf = [0.99] * len(raw)
        conf[6] = 0.10                      # the recogniser doubted the 'Z'
        c = correct_field(raw, "6", confidences=conf)
        assert c.value == "L898902C3"
        assert c.trustworthy


class TestNeverSilentlyWrong:
    """The core safety property of the extraction pipeline.

    Recovering the original value is nice. NEVER returning a wrong value while
    claiming it is verified is mandatory: a passport number that is wrong but
    flagged check-digit-verified would propagate straight into a legal
    document.

    One caveat is mathematical rather than a defect. A mod-10 check digit
    carries a single decimal digit, so about one corruption in ten leaves the
    check digit intact by coincidence. Such a value is indistinguishable from a
    correct one *within that field*. Detecting it requires an independent
    constraint -- the composite check digit spanning several fields, or
    cross-checking the MRZ against the printed visual zone. Both exist in this
    codebase precisely because of this floor.
    """

    def _corrupt(self, rng, original, n=2):
        positions = [i for i, ch in enumerate(original) if ch in CONFUSION]
        if len(positions) < n:
            return None
        picked = rng.sample(positions, n)
        chars = list(original)
        for p in picked:
            chars[p] = rng.choice(sorted(CONFUSION[original[p]]))
        out = "".join(chars)
        return None if out == original else out

    def test_no_wrong_repairs(self):
        """Excluding undetectable collisions, repairs are never wrongly trusted."""
        rng = random.Random(1234)
        originals = ["D23145890", "L898902C3", "AA1234567", "AB9876543",
                     "C01234567", "KA7654321", "M12345678"]
        wrong = ambiguous = attempts = 0

        for original in originals:
            cd = str(check_digit(original))
            for _ in range(60):
                corrupted = self._corrupt(rng, original)
                if corrupted is None:
                    continue
                # Skip the mathematically undetectable case: the corrupted
                # value already satisfies its own check digit.
                if check_digit(corrupted) == int(cd):
                    continue
                attempts += 1
                c = correct_field(corrupted, cd)
                if c.trustworthy and c.value != original:
                    wrong += 1
                if c.ambiguous:
                    ambiguous += 1

        assert attempts > 100
        assert wrong == 0, (
            f"{wrong}/{attempts} values were wrong yet flagged trustworthy - "
            "this is exactly the failure mode the design forbids"
        )
        assert ambiguous > 0, "ambiguity detection appears to be inactive"

    def test_undetectable_collision_rate_is_near_theoretical_floor(self):
        """Quantify the residual risk instead of pretending it is zero."""
        rng = random.Random(7)
        originals = ["D23145890", "L898902C3", "AA1234567", "AB9876543"]
        collisions = attempts = 0

        for original in originals:
            cd = check_digit(original)
            for _ in range(200):
                corrupted = self._corrupt(rng, original)
                if corrupted is None:
                    continue
                attempts += 1
                if check_digit(corrupted) == cd:
                    collisions += 1

        rate = collisions / attempts
        # ~1/10 is the information-theoretic limit of a single mod-10 digit.
        assert 0.03 < rate < 0.20, f"unexpected collision rate {rate:.1%}"

    def test_composite_catches_what_a_single_field_cannot(self):
        """The composite check digit is the answer to the collision floor."""
        good = parse_td3(ICAO_TD3)
        assert good.all_valid

        # Corrupt the document number so its OWN check digit still passes,
        # by editing the check digit to match. The composite must still fail.
        from packages.schema.validators import check_digit as cd
        forged = "L898902C4"
        line2 = forged + str(cd(forged)) + ICAO_TD3[1][10:]
        r = parse_td3([ICAO_TD3[0], line2])
        assert not r.all_valid, "composite failed to catch a field-level forgery"
        assert any("nazorat" in w for w in r.warnings)

    def test_single_error_recovery_is_high(self):
        """One corrupted character is the realistic common case.

        With the recogniser flagging the position it was unsure about, measured
        recovery sits around 80%. The remainder are cases where a different
        position also yields a one-edit solution, so the repair is left
        uncorroborated and routed to human review rather than guessed.
        """
        originals = ["D23145890", "L898902C3", "AA1234567", "AB9876543"]
        recovered = attempts = 0

        for original in originals:
            cd = str(check_digit(original))
            for pos, ch in enumerate(original):
                for repl in sorted(CONFUSION.get(ch, ())):
                    chars = list(original)
                    chars[pos] = repl
                    corrupted = "".join(chars)
                    if corrupted == original:
                        continue
                    attempts += 1
                    conf = [0.99] * len(original)
                    conf[pos] = 0.2
                    c = correct_field(corrupted, cd, confidences=conf)
                    if c.trustworthy and c.value == original:
                        recovered += 1

        rate = recovered / attempts if attempts else 0
        assert attempts > 40
        assert rate > 0.75, f"single-error recovery {rate:.1%} below 75%"


class TestParseTD3:
    def test_parses_icao_sample(self):
        r = parse_td3(ICAO_TD3)
        assert r.found and r.format == "TD3"
        assert r.all_valid, r.warnings

    def test_extracts_person(self):
        r = parse_td3(ICAO_TD3)
        assert r.person.name.surname_latin.value == "Eriksson"
        assert r.person.name.given_name_latin.value == "Anna"
        assert r.person.birth_date.value == "1974-08-12"
        assert r.person.sex.value == "F"
        assert r.person.nationality.value == "UTO"

    def test_document_fields(self):
        r = parse_td3(ICAO_TD3)
        assert r.document is not None
        assert r.document.doc_number.value == "L898902C3"
        assert r.document.doc_number.validated is True
        assert r.document.expiry_date.value == "2012-04-15"

    def test_wrong_check_digit_never_yields_a_validated_wrong_value(self):
        bad = list(ICAO_TD3)
        bad[1] = "L898902C3" + "1" + bad[1][10:]   # check digit says 1, not 6
        r = parse_td3(bad)
        fv = r.document.doc_number
        # Either it is not validated, or it is validated and genuinely matches
        # a value consistent with the (corrupted) check digit - never a value
        # asserted as verified while contradicting the document.
        if fv.validated:
            from packages.schema.validators import check_digit as cd
            assert cd(fv.value.replace(" ", "")) == 1

    def test_cyrillic_derived_not_marked_validated(self):
        r = parse_td3(ICAO_TD3)
        cyr = r.person.name.surname_cyrillic
        assert cyr.value is not None
        assert cyr.validated is False
        assert cyr.source.value == "derived"


class TestParseTD1:
    def test_parses_icao_sample(self):
        r = parse_td1(ICAO_TD1)
        assert r.found and r.format == "TD1"
        assert r.all_valid, r.warnings

    def test_extracts_fields(self):
        r = parse_td1(ICAO_TD1)
        assert r.document.doc_number.value == "D23145890"
        assert r.person.birth_date.value == "1974-08-12"
        assert r.person.name.surname_latin.value == "Eriksson"

    def test_pinfl_extracted_from_optional_data(self):
        lines = list(ICAO_TD1)
        # Put a structurally valid PINFL in the optional data field.
        pinfl = "31503950012345"
        l1 = lines[0][:15] + pinfl + "<"
        lines[0] = l1[:30]
        r = parse_td1(lines)
        assert r.person.pinfl.value == pinfl
        # Structure only — the control digit algorithm is unverified.
        assert r.person.pinfl.validated is False


class TestParseDispatch:
    def test_three_lines_to_td1(self):
        assert parse_mrz(ICAO_TD1).format == "TD1"

    def test_two_lines_to_td3(self):
        assert parse_mrz(ICAO_TD3).format == "TD3"

    def test_bad_line_count_returns_not_found(self):
        r = parse_mrz(["one"])
        assert not r.found and r.reason is not None

    def test_never_raises_on_garbage(self):
        for junk in ([], ["", ""], ["!!!", "???", "***"], ["a" * 100] * 3):
            r = parse_mrz(junk)
            assert isinstance(r.found, bool)


class TestNoSilentGuessing:
    """The system must never emit a validated value that is wrong."""

    def test_unreadable_field_is_none_not_guessed(self):
        lines = list(ICAO_TD3)
        lines[1] = "<<<<<<<<<0" + lines[1][10:]
        r = parse_td3(lines)
        assert r.document.doc_number.value is None \
            or r.document.doc_number.validated is False
