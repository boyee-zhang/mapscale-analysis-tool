"""
Tests for the geocode_cache primary key normalisation.

normalise_key's output IS the primary key of geocode_cache, so any change to its
rules silently invalidates every cached row. These tests pin the rules down.
"""
import pytest

from api.main.domains.housing.geocoder import _normalise_address, normalise_key


@pytest.mark.parametrize("raw, expected", [
    ("Kastanjelaan 1 Eindhoven", "kastanjelaan 1 eindhoven"),
    # case
    ("KASTANJELAAN 1 EINDHOVEN", "kastanjelaan 1 eindhoven"),
    # punctuation dropped
    ("Jan van Zutphenstraat 919, Amsterdam", "jan van zutphenstraat 919 amsterdam"),
    ("'s-Gravenhage 12", "s gravenhage 12"),
    # whitespace collapsed and trimmed
    ("  Dorpsstraat   4\tUtrecht \n", "dorpsstraat 4 utrecht"),
    # Dutch postcode: internal space removed, lowercased
    ("Dorpsstraat 4 1234 AB Utrecht", "dorpsstraat 4 1234ab utrecht"),
    ("Dorpsstraat 4 1234AB Utrecht", "dorpsstraat 4 1234ab utrecht"),
])
def test_normalise_key(raw, expected):
    assert normalise_key(raw) == expected


def test_normalise_key_is_idempotent():
    once = normalise_key("Dorpsstraat 4, 1234 AB  Utrecht")
    assert normalise_key(once) == once


def test_variants_of_the_same_address_collapse():
    keys = {
        normalise_key("Kastanjelaan 1, Eindhoven"),
        normalise_key("kastanjelaan  1 eindhoven"),
        normalise_key("Kastanjelaan 1 Eindhoven."),
    }
    assert len(keys) == 1


def test_different_house_numbers_do_not_collapse():
    assert normalise_key("Kastanjelaan 1 Eindhoven") != normalise_key("Kastanjelaan 2 Eindhoven")


@pytest.mark.parametrize("name, city, expected", [
    ("Kastanjelaan 1-108, Eindhoven", "Eindhoven", "Kastanjelaan 1 Eindhoven"),
    ("Jan van Zutphenstraat 919, Amsterdam", "Amsterdam", "Jan van Zutphenstraat 919 Amsterdam"),
    # city suffix is stripped case-insensitively, then re-appended canonically
    ("Dorpsstraat 4, AMSTERDAM", "Amsterdam", "Dorpsstraat 4 Amsterdam"),
    # no city suffix present
    ("Dorpsstraat 4", "Utrecht", "Dorpsstraat 4 Utrecht"),
    # H2S names PDOK does not index — querying "The Hague" matches Zwolle
    ("Spui 1, The Hague", "The Hague", "Spui 1 's-Gravenhage"),
    ("Markt 1, Den Bosch", "Den Bosch", "Markt 1 's-Hertogenbosch"),
])
def test_normalise_address_builds_pdok_query(name, city, expected):
    assert _normalise_address(name, city) == expected
