from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.rights import NOT_AUTHORIZED, PROOF_REQUIRED, publication_error
from app.routers.film_management import FilmInput, local_today, rights_error

TODAY = date(2026, 10, 29)


def test_rights_error_rules():
    assert rights_error("authorized", "", None, TODAY)
    assert rights_error("authorized", "Distribuidora", TODAY - timedelta(days=1), TODAY)
    assert rights_error("authorized", "Distribuidora", TODAY, TODAY) is None
    assert rights_error("authorized", "Distribuidora", None, TODAY) is None
    # public_domain dispensa titular/validade; pending/denied não exigem nada.
    for status in ("public_domain", "pending", "denied", None):
        assert rights_error(status, "", TODAY - timedelta(days=10), TODAY) is None


def test_film_input_rights_group():
    film = FilmInput(title=" Filme ")
    assert film.rights_status is None and film.rights() == ("pending", "", "", None, "")
    authorized = FilmInput(
        title="Filme",
        rights_status="authorized",
        rights_holder="  Distribuidora  ",
        license_type="educacional",
        rights_valid_until=local_today(),
    )
    assert authorized.rights()[:3] == ("authorized", "Distribuidora", "educacional")
    for bad in (
        {"rights_status": "authorized", "rights_holder": " "},
        {"rights_status": "authorized", "rights_holder": "X", "rights_valid_until": local_today() - timedelta(days=1)},
        {"rights_status": "liberado"},
        {"rights_status": "public_domain", "rights_holder": "x" * 201},
        {"rights_status": "public_domain", "license_type": "x" * 81},
        {"rights_status": "public_domain", "rights_notes": "x" * 2001},
    ):
        with pytest.raises(ValidationError):
            FilmInput(title="Filme", **bad)


def test_publication_rule():
    ends = date(2026, 12, 1)
    assert publication_error("pending", None, ends, True) == NOT_AUTHORIZED
    assert publication_error("denied", None, ends, True) == NOT_AUTHORIZED
    assert publication_error("authorized", ends - timedelta(days=1), ends, True) == NOT_AUTHORIZED
    assert publication_error("authorized", ends, ends, False) == PROOF_REQUIRED
    assert publication_error("authorized", ends, ends, True) is None
    assert publication_error("authorized", None, ends, True) is None
    assert publication_error("public_domain", None, ends, False) is None
    assert publication_error("public_domain", ends - timedelta(days=1), ends, False) == NOT_AUTHORIZED
