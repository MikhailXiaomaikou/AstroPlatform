from __future__ import annotations


def _dataset(service_key: str, *, template: str | None = None, article: str = "2023A&A...674A...1G") -> dict:
    return {
        "service_key": service_key,
        "service_name": service_key.upper(),
        "article": article,
        "archive_version": f"{service_key}-archive",
        "credits_page_url": f"https://example.test/{service_key}/credits",
        "source_urls": [f"https://example.test/{service_key}/data"],
        **({"acknowledgement_template": template} if template else {}),
    }


def _messages_with_tool_result(tool_result: dict) -> list[dict]:
    return [
        {"role": "user", "content": "Analyze a catalog result."},
        {
            "role": "assistant",
            "content": "The catalog value is cited as 2023A&A...674A...1G.",
            "actions": [
                {
                    "action": "run_adql",
                    "service": "gaia",
                    "query": "SELECT TOP 1 * FROM gaiadr3.gaia_source",
                    "tool_result": tool_result,
                }
            ],
        },
    ]


def test_single_tool_call_vizier_acknowledgement_uses_registry_template():
    from app.services.paper_generator import _build_default_paper_json, _extract_actions

    tool_result = {
        "provenance": {
            "reproducibility": {
                "run_id": "run-vizier",
                "query_hash": "hash-vizier",
                "archive_version": "VizieR live",
                "tool_version": "dev",
            },
            "datasets": [
                _dataset("vizier", template="The VizieR catalogue access tool, CDS, Strasbourg, France.")
            ],
        }
    }
    artifacts = _extract_actions(_messages_with_tool_result(tool_result))
    paper_json = _build_default_paper_json(artifacts, "aastex")

    assert "VizieR catalogue access tool" in paper_json["acknowledgments"]
    assert "run_id=run-vizier" in paper_json["data_provenance"]
    assert "archive_version=VizieR live" in paper_json["data_provenance"]
    assert "https://example.test/vizier/data" in paper_json["data_provenance"]


def test_multi_source_acknowledgement_includes_each_dataset_template():
    from app.services.paper_generator import _build_default_paper_json, _extract_actions

    tool_result = {
        "provenance": {
            "datasets": [
                _dataset("vizier", template="VizieR acknowledgement."),
                _dataset("simbad", template="SIMBAD acknowledgement.", article="2000A&AS..143....9W"),
                _dataset("gaia", template="Gaia acknowledgement."),
            ]
        }
    }

    paper_json = _build_default_paper_json(_extract_actions(_messages_with_tool_result(tool_result)), "aastex")

    assert "VizieR acknowledgement." in paper_json["acknowledgments"]
    assert "SIMBAD acknowledgement." in paper_json["acknowledgments"]
    assert "Gaia acknowledgement." in paper_json["acknowledgments"]


def test_unknown_registry_template_falls_back_to_hardcoded_acknowledgement():
    from app.services.paper_generator import _build_acknowledgments

    acknowledgement = _build_acknowledgments(["sdss"], datasets=[{"service_key": "sdss"}])

    assert "Sloan Digital Sky Survey" in acknowledgement


def test_latex_output_contains_data_provenance_and_source_urls():
    from app.services.paper_generator import _build_default_paper_json, _extract_actions, render_latex

    tool_result = {
        "source_urls": ["https://example.test/raw-source"],
        "provenance": {
            "reproducibility": {
                "run_id": "run-123",
                "query_hash": "hash-123",
                "archive_version": "Gaia DR3",
                "tool_version": "dev",
            },
            "datasets": [_dataset("gaia", template="Gaia acknowledgement.")],
        },
    }

    paper_json = _build_default_paper_json(_extract_actions(_messages_with_tool_result(tool_result)), "aastex")
    latex = render_latex(paper_json, "aastex")

    assert "% Data provenance: tool=run_adql; run_id=run-123; query_hash=hash-123; archive_version=Gaia DR3; tool_version=dev" in latex
    assert r"\section{Data provenance}" in latex
    assert "https://example.test/raw-source" in latex
