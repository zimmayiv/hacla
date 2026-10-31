import pytest

from webapp.app import create_app


@pytest.fixture(scope="module")
def client():
    return create_app().test_client()


@pytest.mark.parametrize("path", ["/", "/entities/", "/sources/", "/issues/", "/people/", "/entity/la-cienega/", "/entity/lomod-pds/", "/person/cielo-castro/", "/404.html", "/data/holdings.json", "/data/stats.json", "/data/people.json", "/data/entities.json", "/api/holdings?limit=5", "/api/entities/hoc/board"])
def test_pages_load(client, path):
    assert client.get(path).status_code == 200


def test_local_api_still_filters(client):
    data = client.get("/api/holdings?program=PSH&ami_at_most=30&limit=500").get_json()
    assert data["total"] == len(data["rows"]) and all("PSH" in r["programs"] for r in data["rows"])


def test_source_link_opens_correct_page(client):
    html = client.get("/holding/clarendon-apartments/").get_data(as_text=True)
    assert "2024.11.14%20Item%20A3-%20Clarendon%20Apartments%20Acquisition.pdf#page=" in html
    assert "HACLA Audited Financial Statements" in html


def test_weak_evidence_is_labelled(client):
    assert "search excerpt" in client.get("/holding/union-tower/").get_data(as_text=True)


def test_bad_api_filter_is_an_error(client):
    assert client.get("/api/holdings?program=Nope").status_code == 400


def test_missing_things_404(client):
    for path in ("/holding/nope/", "/entity/nope/", "/person/nobody/", "/data/holding/nope.json", "/api/entities/nope/board"):
        assert client.get(path).status_code == 404


def test_board_on_holding_and_entity_pages(client):
    html = client.get("/holding/clarendon-apartments/").get_data(as_text=True)
    assert "Who governs the holding entities" in html and "Jenny Scanlin" in html and "/person/jenny-scanlin/" in html
    html = client.get("/entity/housing-pathways/").get_data(as_text=True)
    assert "Board and officers" in html and "appointee" in html


def test_index_works_without_javascript(client):
    html = client.get("/").get_data(as_text=True)
    assert html.count("<tr>") >= 177 and "Turn on JavaScript" in html
