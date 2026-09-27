"""Tests for bank-description → merchant-name normalisation."""

import pytest

from portf_manager.services.merchant import normalize_merchant


@pytest.mark.parametrize(
    "description, expected",
    [
        ("123456789012SUPERMERCAT EXEMPLE  \\TIANA\\ES26010112", "SUPERMERCAT EXEMPLE"),
        ("123456789012EXAMPLE SHOP MADRID 010012345", "EXAMPLE SHOP MADRID"),
        ("123456789012AMAZON* AB12CD34E LUXEMBOURG 010012345", "AMAZON LUXEMBOURG"),
        ("123456789012PAYPAL *EXAMPLE STORE BE 12345678901 00000", "EXAMPLE STORE BE"),
        (
            "123456789012SumUp  *Cafe Exemple Barcelona 010012345",
            "Cafe Exemple Barcelona",
        ),
        ("LIQUIDACIO TARGETA CREDIT *1234 07/25", "LIQUIDACIO TARGETA CREDIT"),
        ("R/ Example Insurance S.A.", "Example Insurance S.A."),
        ("123456-001Example AG", "Example AG"),
        ("CARGO EXAMPLE LOAN 1234-567890-123 45-", "CARGO EXAMPLE LOAN"),
        ("BIZUM ENVIADO: Example Person", "BIZUM ENVIADO: Example Person"),
    ],
)
def test_normalize_merchant(description, expected):
    assert normalize_merchant(description) == expected


def test_all_digit_description_falls_back():
    assert normalize_merchant("123456789") == "123456789"


def test_blank_description_stays_blank():
    assert normalize_merchant("   ") == ""


def test_whitespace_is_collapsed():
    assert normalize_merchant("BIZUM ENVIADO:  Example   Person ") == (
        "BIZUM ENVIADO: Example Person"
    )


@pytest.mark.parametrize(
    "description",
    [
        "123456789012EXAMPLE SHOP MADRID 010012345",
        "123456789012AMAZON* AB12CD34E LUXEMBOURG 010012345",
        "R/ Example Insurance S.A.",
    ],
)
def test_idempotent(description):
    once = normalize_merchant(description)
    assert normalize_merchant(once) == once
