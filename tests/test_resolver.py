"""Offline acceptance tests A-I: the resolver against saved official API JSON served by respx (no network)."""

from __future__ import annotations

import httpx
import pytest
import respx

from src.models.input import ActorInput
from src.resolver import run_resolution
from tests.conftest import AS_OF, load

FR = "https://www.federalregister.gov/api/v1"
REGS = "https://api.regulations.gov/v4"


def _fr_routes(mock: respx.MockRouter) -> None:
    rin_files = {
        r: load(f"fr/rin-{r}.json")
        for r in ("2060-AV16", "1903-AA23", "1235-AA52", "2060-AW66", "1904-AG06", "0938-AV77")
    }
    docs = {d["document_number"]: d for f in rin_files.values() for d in f["results"]}
    docket = load("fr/docket-EPA-HQ-OAR-2021-0317.json")
    docs.update({d["document_number"]: d for d in docket["results"]})

    def by_number(request: httpx.Request) -> httpx.Response:
        number = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        return httpx.Response(200, json=docs[number]) if number in docs else httpx.Response(404, json={})

    def search(request: httpx.Request) -> httpx.Response:
        q = request.url.params
        if rin := q.get("conditions[regulation_id_number]"):
            return httpx.Response(200, json=rin_files.get(rin, {"count": 0, "results": []}))
        if q.get("conditions[docket_id]") == "EPA-HQ-OAR-2021-0317":
            return httpx.Response(200, json=docket)
        if q.get("conditions[docket_id]"):
            return httpx.Response(200, json={"count": 0, "results": []})
        return httpx.Response(200, json=load("fr/search-methane.json"))

    mock.get(url__regex=rf"{FR}/documents/[0-9-]+\.json").mock(side_effect=by_number)
    mock.get(f"{FR}/documents.json").mock(side_effect=search)


def _regs_ok(mock: respx.MockRouter) -> None:
    mock.get(f"{REGS}/dockets/EPA-HQ-OAR-2021-0317").respond(200, json=load("regs/docket-EPA-HQ-OAR-2021-0317.json"))
    mock.get(f"{REGS}/documents").respond(200, json=load("regs/documents-EPA-HQ-OAR-2021-0317.json"))
    mock.get(url__regex=rf"{REGS}/dockets/.+").respond(404, json={})


async def resolve(payload: dict, monkeypatch: pytest.MonkeyPatch, *, regs: str = "ok"):
    monkeypatch.setenv("REGULATIONS_GOV_API_KEY", "test-key")
    with respx.mock(assert_all_called=False) as mock:
        _fr_routes(mock)
        if regs == "ok":
            _regs_ok(mock)
        else:
            mock.get(url__regex=rf"{REGS}/.*").respond(503)
        async with httpx.AsyncClient() as client:
            return (await run_resolution(ActorInput.model_validate(payload), client=client, today=AS_OF)).result


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.utils.http.backoff_delay", lambda attempt: 0)


async def test_a_exact_document_number(monkeypatch) -> None:
    r = await resolve({"federalRegisterDocumentNumber": "2024-00366"}, monkeypatch)
    assert (r.status, r.resolution.confidence, r.lifecycle.current_stage) == ("resolved", "high", "effective")
    assert r.action.rins == ["2060-AV16"]
    assert r.regulations_gov.dockets[0].docket_id == "EPA-HQ-OAR-2021-0317"
    assert r.billing.billable
    numbers = [d.document_number for d in r.related_documents]
    assert len(numbers) == len(set(numbers)) == 4
    final = next(d for d in r.related_documents if d.document_number == "2024-00366")
    assert final.relation_basis[0] == "requested Federal Register document number"
    assert final.regulations_gov_document_id == "EPA-HQ-OAR-2021-0317-3853"
    assert all(d.source_url and d.relation_basis for d in r.related_documents)


async def test_a_url_input_matches_document_number(monkeypatch) -> None:
    url = "https://www.federalregister.gov/documents/2024/03/08/2024-00366/standards-of-performance"
    r = await resolve({"federalRegisterUrl": url}, monkeypatch)
    assert r.status == "resolved"
    assert r.query.input_type == "federal_register_url"


async def test_b_rin(monkeypatch) -> None:
    r = await resolve({"rin": "2060-av16"}, monkeypatch)
    assert (r.status, r.resolution.confidence) == ("resolved", "high")
    assert r.resolution.matched_by == ["RIN"]
    assert r.regulations_gov.document_count == 5


async def test_c_docket_excludes_other_rin(monkeypatch) -> None:
    r = await resolve({"docketId": "EPA-HQ-OAR-2021-0317"}, monkeypatch)
    assert r.status == "resolved"
    assert r.lifecycle.current_stage == "effective"
    assert "2024-13206" not in [d.document_number for d in r.related_documents]
    kinds = {a.kind for a in r.resolution.ambiguities}
    assert {"multiple_rins_in_docket", "excluded_document"} <= kinds


async def test_d_and_e_broad_natural_language_is_ambiguous_and_free(monkeypatch) -> None:
    r = await resolve({"query": "EPA methane rule"}, monkeypatch)
    assert r.status == "ambiguous"
    assert 3 <= len(r.candidates) <= 5
    assert r.lifecycle is None
    assert not r.billing.billable


async def test_f_comment_extension(monkeypatch) -> None:
    r = await resolve({"federalRegisterDocumentNumber": "2021-27312"}, monkeypatch)
    assert r.lifecycle.comment_period.extended is True
    assert r.lifecycle.comment_period.original_deadline == "2022-01-14"


async def test_g_effective_date_delay(monkeypatch) -> None:
    r = await resolve({"rin": "1903-AA23"}, monkeypatch)
    assert r.lifecycle.effective_date.delayed is True
    assert r.lifecycle.effective_date.current == "2026-12-24"
    assert r.lifecycle.current_stage == "final_rule_not_yet_effective"


async def test_h_regulations_gov_outage_gives_billable_partial(monkeypatch) -> None:
    r = await resolve({"rin": "2060-AV16"}, monkeypatch, regs="down")
    assert r.status == "partial"
    assert r.sources.regulations_gov.status == "unavailable"
    assert r.sources.federal_register.status == "success"
    assert r.lifecycle.current_stage == "effective"
    assert r.regulations_gov is None
    assert r.billing.billable


async def test_h_federal_register_outage_is_not_absence(monkeypatch) -> None:
    monkeypatch.setenv("REGULATIONS_GOV_API_KEY", "test-key")
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=rf"{FR}/.*").respond(503)
        async with httpx.AsyncClient() as client:
            r = (
                await run_resolution(ActorInput.model_validate({"rin": "2060-AV16"}), client=client, today=AS_OF)
            ).result
    assert r.status == "insufficient_data"
    assert r.sources.federal_register.status == "unavailable"
    assert not r.billing.billable


async def test_missing_key_is_reported_not_hidden(monkeypatch) -> None:
    monkeypatch.delenv("REGULATIONS_GOV_API_KEY", raising=False)
    with respx.mock(assert_all_called=False) as mock:
        _fr_routes(mock)
        async with httpx.AsyncClient() as client:
            r = (
                await run_resolution(ActorInput.model_validate({"rin": "2060-AV16"}), client=client, today=AS_OF)
            ).result
    assert r.status == "partial"
    assert r.sources.regulations_gov.status == "authentication_failed"


async def test_i_conflicting_identifiers(monkeypatch) -> None:
    r = await resolve({"rin": "1235-AA52", "federalRegisterDocumentNumber": "2024-00366"}, monkeypatch)
    assert r.status == "conflicting_identifiers"
    assert r.lifecycle is None
    assert not r.billing.billable


async def test_agreeing_identifiers(monkeypatch) -> None:
    r = await resolve(
        {"rin": "2060-AV16", "docketId": "EPA-HQ-OAR-2021-0317", "federalRegisterDocumentNumber": "2024-00366"},
        monkeypatch,
    )
    assert r.status == "resolved"
    assert set(r.resolution.matched_by) == {"federal_register_document_number", "RIN", "docket_id"}


async def test_not_found(monkeypatch) -> None:
    r = await resolve({"rin": "9999-ZZ99"}, monkeypatch)
    assert r.status == "not_found"
    assert not r.billing.billable


async def test_withdrawn_and_correction(monkeypatch) -> None:
    assert (await resolve({"rin": "1235-AA52"}, monkeypatch)).lifecycle.current_stage == "withdrawn"
    r = await resolve({"federalRegisterDocumentNumber": "2026-20134"}, monkeypatch)
    assert r.lifecycle.current_stage == "effective"
    assert r.related_documents[-1].normalized_type == "correction"
