from tests.conftest import make_story


def test_health(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_empty_list(client):
    data = client.get("/api/v1/stories").json()
    assert data["items"] == []
    assert data["total_items"] == 0
    assert data["total_pages"] == 1


def test_pagination_and_order(client, db_session):
    for i in range(12):
        make_story(db_session, i)

    page1 = client.get("/api/v1/stories?page=1&page_size=9").json()
    assert len(page1["items"]) == 9
    assert page1["total_items"] == 12
    assert page1["total_pages"] == 2
    # Orden: más reciente primero (índice 11 tiene published_at mayor)
    assert "número 11" in page1["items"][0]["title"]

    page2 = client.get("/api/v1/stories?page=2&page_size=9").json()
    assert len(page2["items"]) == 3


def test_list_item_shape(client, db_session):
    make_story(db_session, 1, extra_sources=2, synthesized=True)
    item = client.get("/api/v1/stories").json()["items"][0]
    assert item["sources_count"] == 3
    assert item["has_synthesized"] is True
    assert item["classification"]["label"] == "populismo"
    assert abs(sum(item["classification"]["probabilities"].values()) - 1.0) < 0.001


def test_filter_by_source_category(client, db_session):
    make_story(db_session, 1, source_category="nacional")
    make_story(db_session, 2, source_category="judicial", source_name="expediente")

    data = client.get("/api/v1/stories?source_category=judicial").json()
    assert data["total_items"] == 1
    assert data["items"][0]["source_category"] == "judicial"

    bad = client.get("/api/v1/stories?source_category=inexistente")
    assert bad.status_code == 400


def test_search(client, db_session):
    make_story(db_session, 1, title="Reforma pensional entra en conciliación")
    make_story(db_session, 2, title="Debate sobre seguridad en la frontera")

    data = client.get("/api/v1/stories?q=pensional").json()
    assert data["total_items"] == 1
    assert "pensional" in data["items"][0]["title"].lower()


def test_search_accent_insensitive(client, db_session):
    make_story(db_session, 1, title="Última hora del Congreso")
    # Sin tilde y en otra caja debe encontrar igual
    data = client.get("/api/v1/stories?q=ULTIMA").json()
    assert data["total_items"] == 1
    # Con tilde también
    data = client.get("/api/v1/stories?q=última").json()
    assert data["total_items"] == 1


def test_search_like_wildcards_escaped(client, db_session):
    make_story(db_session, 1, title="Reforma pensional")
    # "%" no debe actuar como comodín universal
    data = client.get("/api/v1/stories?q=%25").json()
    assert data["total_items"] == 0


def test_empty_source_category_ignored(client, db_session):
    make_story(db_session, 1)
    response = client.get("/api/v1/stories?source_category=")
    assert response.status_code == 200
    assert response.json()["total_items"] == 1


def test_unpublished_story_hidden(client, db_session):
    make_story(db_session, 1, classified=False)
    data = client.get("/api/v1/stories").json()
    assert data["total_items"] == 0


def test_detail_single_source(client, db_session):
    story = make_story(db_session, 1)
    data = client.get(f"/api/v1/stories/{story.slug}").json()
    assert data["synthesized"] is None
    assert len(data["sources"]) == 1
    source = data["sources"][0]
    assert source["snippet"]
    assert "text" not in source  # el body completo NUNCA se expone
    assert source["classification"]["label"] == "populismo"


def test_detail_with_synthesized(client, db_session):
    story = make_story(db_session, 1, synthesized=True, extra_sources=3)
    data = client.get(f"/api/v1/stories/{story.slug}").json()
    assert data["synthesized"]["body"] == ["Párrafo uno.", "Párrafo dos."]
    assert "generado" in data["synthesized"]["disclaimer"].lower()
    assert len(data["sources"]) == 4


def test_detail_404(client):
    response = client.get("/api/v1/stories/no-existe")
    assert response.status_code == 404
