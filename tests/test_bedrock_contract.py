"""Sanity checks for the bedrock test metadata layer."""

from __future__ import annotations

from .bedrock import BedrockCase, bedrock_case


def test_bedrock_case_rejects_invalid_reliance_percentage():
    try:
        bedrock_case(
            "invalid.case",
            purpose="reject invalid percentages",
            use_case="sanity checking",
            similar_use_cases=["metadata validation"],
            reliance_percent=0,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("bedrock_case should reject 0% reliance.")


def test_bedrock_case_preserves_metadata_on_wrapped_test():
    @bedrock_case(
        "router.example",
        purpose="document a critical contract case",
        use_case="metadata inspection",
        similar_use_cases=["report output", "bedrock summaries"],
        reliance_percent=99.0,
    )
    def sample_case():
        return None

    case = getattr(sample_case, "__bedrock_case__", None)

    assert isinstance(case, BedrockCase)
    assert case.case_id == "router.example"
    assert case.purpose == "document a critical contract case"
    assert case.use_case == "metadata inspection"
    assert case.similar_use_cases == ("report output", "bedrock summaries")
    assert case.reliance_percent == 99.0
