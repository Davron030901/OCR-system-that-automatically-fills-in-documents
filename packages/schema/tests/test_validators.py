"""Tests for the deterministic validators.

The MRZ samples below are the canonical examples from ICAO Doc 9303. If these
ever fail, the check-digit implementation is wrong and every downstream
accuracy claim in the project is void.
"""

from datetime import date

import pytest

from packages.schema.translit import (
    TURNED_COMMA,
    cyrillic_to_latin,
    latin_to_cyrillic,
    normalize_apostrophes,
    to_mrz_name,
)
from packages.schema.validators import (
    char_value,
    check_digit,
    cross_check,
    parse_mrz_date,
    validate_date_logic,
    validate_pinfl,
    validate_td1,
    validate_td3,
)

# ICAO 9303 Part 3 worked example.
ICAO_TD3 = [
    "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<",
    "L898902C36UTO7408122F1204159ZE184226B<<<<<10",
]

# ICAO 9303 Part 5 worked example.
ICAO_TD1 = [
    "I<UTOD231458907<<<<<<<<<<<<<<<",
    "7408122F1204159UTO<<<<<<<<<<<6",
    "ERIKSSON<<ANNA<MARIA<<<<<<<<<<",
]


class TestCheckDigit:
    def test_char_values(self):
        assert char_value("0") == 0
        assert char_value("9") == 9
        assert char_value("A") == 10
        assert char_value("Z") == 35
        assert char_value("<") == 0

    def test_invalid_char_raises(self):
        with pytest.raises(ValueError):
            char_value("!")

    @pytest.mark.parametrize("value,expected", [
        ("D23145890", 7),      # ICAO TD1 document number
        ("740812", 2),         # ICAO birth date
        ("120415", 9),         # ICAO expiry date
        ("L898902C3", 6),      # ICAO TD3 document number
    ])
    def test_known_check_digits(self, value, expected):
        assert check_digit(value) == expected

    def test_weights_cycle_731(self):
        # A single '1' in each of the first three positions exposes the weights.
        assert check_digit("1") == 7
        assert check_digit("01") == 3
        assert check_digit("001") == 1


class TestTD3:
    def test_icao_sample_fully_valid(self):
        v = validate_td3(ICAO_TD3)
        assert v.ok, v.errors
        assert v.all_check_digits_pass

    def test_fields_extracted(self):
        v = validate_td3(ICAO_TD3)
        assert v.fields["doc_number"].raw == "L898902C3"
        assert v.fields["birth_date"].raw == "740812"
        assert v.fields["expiry_date"].raw == "120415"
        assert v.fields["nationality"].raw == "UTO"

    def test_corrupted_digit_detected(self):
        bad = list(ICAO_TD3)
        bad[1] = "L898902C3" + "7" + bad[1][10:]   # wrong check digit
        v = validate_td3(bad)
        assert not v.ok
        assert any("doc_number" in e for e in v.errors)

    def test_wrong_line_count(self):
        v = validate_td3(["only one line"])
        assert not v.ok


class TestTD1:
    def test_icao_sample_fully_valid(self):
        v = validate_td1(ICAO_TD1)
        assert v.ok, v.errors

    def test_fields_extracted(self):
        v = validate_td1(ICAO_TD1)
        assert v.fields["doc_number"].raw == "D23145890"
        assert v.fields["birth_date"].raw == "740812"
        assert v.fields["expiry_date"].raw == "120415"

    def test_corrupted_birth_date_detected(self):
        bad = list(ICAO_TD1)
        bad[1] = "740813" + bad[1][6:]   # date changed, check digit stale
        v = validate_td1(bad)
        assert not v.ok


class TestPINFL:
    def test_rejects_wrong_length(self):
        ok, info = validate_pinfl("123")
        assert not ok
        assert "14 digits" in info["errors"][0]

    def test_rejects_non_digits(self):
        ok, _ = validate_pinfl("5150395123456A")
        assert not ok

    def test_decodes_structure(self):
        # marker 5 -> male, born 2000s; 150395 -> 15 March 2095... using
        # marker 3 (1900s male) for a realistic date.
        ok, info = validate_pinfl("31503950012345")
        assert ok
        assert info["sex"] == "M"
        assert info["birth_date"] == date(1995, 3, 15)
        assert info["structure_valid"]

    def test_female_marker(self):
        ok, info = validate_pinfl("42003920012345")
        assert ok
        assert info["sex"] == "F"

    def test_checksum_never_claimed_verified(self):
        """The control-digit algorithm is an open TODO; never claim otherwise."""
        ok, info = validate_pinfl("31503950012345")
        assert ok
        assert info["checksum_verified"] is False

    def test_invalid_date_rejected(self):
        ok, info = validate_pinfl("33213950012345")   # month 39
        assert not ok


class TestDateLogic:
    def test_expiry_before_issue(self):
        w = validate_date_logic(date(1990, 1, 1), date(2020, 1, 1),
                                date(2015, 1, 1), today=date(2024, 1, 1))
        assert any("muddati" in x for x in w)

    def test_clean_dates_no_warnings(self):
        w = validate_date_logic(date(1990, 1, 1), date(2020, 1, 1),
                                date(2030, 1, 1), today=date(2024, 1, 1))
        assert w == []

    def test_expired_document_flagged(self):
        w = validate_date_logic(date(1990, 1, 1), date(2010, 1, 1),
                                date(2020, 1, 1), today=date(2024, 1, 1))
        assert any("tugagan" in x for x in w)


class TestMRZDate:
    def test_birth_pivot_past(self):
        assert parse_mrz_date("740812", "birth", today=date(2026, 1, 1)) \
            == date(1974, 8, 12)

    def test_birth_pivot_recent(self):
        assert parse_mrz_date("050301", "birth", today=date(2026, 1, 1)) \
            == date(2005, 3, 1)

    def test_expiry_always_2000s(self):
        assert parse_mrz_date("300101", "expiry") == date(2030, 1, 1)

    def test_invalid_returns_none(self):
        assert parse_mrz_date("991399", "birth") is None


class TestCrossCheck:
    def test_identical_no_warning(self):
        assert cross_check({"doc_number": "AA1234567"},
                           {"doc_number": "AA1234567"}) == []

    def test_number_mismatch_flagged_exactly(self):
        w = cross_check({"pinfl": "31503950012345"},
                        {"pinfl": "31503950012346"})
        assert len(w) == 1

    def test_minor_name_ocr_noise_tolerated(self):
        assert cross_check({"surname": "ALIYEV"}, {"surname": "ALIYEV"}) == []

    def test_different_name_flagged(self):
        w = cross_check({"surname": "ALIYEV"}, {"surname": "KARIMOV"})
        assert len(w) == 1


class TestTranslit:
    def test_all_apostrophe_variants_normalised(self):
        for variant in ["O'zbek", "O\u2018zbek", "O\u2019zbek",
                        "O`zbek", "O\u00B4zbek", "O\u02BCzbek"]:
            assert normalize_apostrophes(variant) == f"O{TURNED_COMMA}zbek"

    def test_latin_to_cyrillic_digraphs(self):
        assert latin_to_cyrillic("Shohruh") == "Шоҳруҳ"
        assert latin_to_cyrillic(f"O{TURNED_COMMA}zbekiston").startswith("Ў")

    def test_cyrillic_round_trip_preserves_special_letters(self):
        assert cyrillic_to_latin("Ғафур") == f"G{TURNED_COMMA}afur"

    def test_mrz_name_folds_specials(self):
        out = to_mrz_name(f"G{TURNED_COMMA}ofurov", "Shohruh", "Akmal")
        assert out == "GOFUROV<<SHOHRUH<AKMAL"
        assert TURNED_COMMA not in out

    def test_none_passthrough(self):
        assert normalize_apostrophes(None) is None
        assert latin_to_cyrillic(None) is None
