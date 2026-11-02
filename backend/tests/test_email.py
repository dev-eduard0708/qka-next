from qka_shared.email_address import try_normalize


def test_normalizes_qehc_address() -> None:
    email, error = try_normalize("  Name@QEHC.EDU.SA ")
    assert error == ""
    assert email == "name@qehc.edu.sa"


def test_rejects_other_domains() -> None:
    email, error = try_normalize("user@gmail.com")
    assert email is None
    assert "qehc.edu.sa" in error
