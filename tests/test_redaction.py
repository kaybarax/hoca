from hoca.redaction import redact_public_evidence_text


def test_redact_public_evidence_text_replaces_local_paths_and_contacts() -> None:
    text = (
        "Project /Users/alice/workspace/private-repo failed with "
        "api_key=abc123 for alice@example.test"
    )

    redacted = redact_public_evidence_text(text)

    assert "/Users/alice" not in redacted
    assert "alice@example.test" not in redacted
    assert "abc123" not in redacted
    assert "<LOCAL_PATH>" in redacted
    assert "<EMAIL>" in redacted
    assert "api_key=<REDACTED>" in redacted
