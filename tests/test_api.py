from datetime import UTC, datetime

from tests.conftest import _uniform_probabilities, make_story


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


# --- Filtros por ideología y fecha (listado del FE con paginación de servidor) ---


def _story_with(db_session, index, *, label="populismo", published=None, **kwargs):
    """make_story con la clase del representativo y la fecha de la story."""
    story = make_story(db_session, index, **kwargs)
    rep = story.articles[0]
    rep.classification_label = label
    rep.classification_probabilities = _uniform_probabilities(label)
    if published is not None:
        story.published_at = published
        rep.published_at = published
    db_session.commit()
    return story


def _labels(data):
    return sorted(item["classification"]["label"] for item in data["items"])


def test_filter_by_ideology(client, db_session):
    _story_with(db_session, 1, label="populismo")
    _story_with(db_session, 2, label="progresismo")
    _story_with(db_session, 3, label="globalismo")

    data = client.get("/api/v1/stories?ideology=progresismo").json()
    assert data["total_items"] == 1
    assert _labels(data) == ["progresismo"]

    # Varias clases: repitiendo el parámetro o separadas por comas
    repeated = client.get("/api/v1/stories?ideology=progresismo&ideology=globalismo").json()
    commas = client.get("/api/v1/stories?ideology=progresismo,globalismo").json()
    assert _labels(repeated) == _labels(commas) == ["globalismo", "progresismo"]
    assert repeated["total_items"] == 2


def test_filter_by_ideology_validation(client, db_session):
    make_story(db_session, 1)
    assert client.get("/api/v1/stories?ideology=anarquismo").status_code == 400
    assert client.get("/api/v1/stories?ideology=populismo,anarquismo").status_code == 400
    # Vacío se ignora, como ?source_category=
    response = client.get("/api/v1/stories?ideology=")
    assert response.status_code == 200
    assert response.json()["total_items"] == 1


def test_filters_reach_beyond_max_page_size(client, db_session):
    # Las 10 más antiguas son progresistas; las 50 más recientes, populistas.
    # Un filtro aplicado sobre las 50 más recientes no encontraría ninguna.
    for i in range(60):
        _story_with(db_session, i, label="progresismo" if i < 10 else "populismo")

    data = client.get("/api/v1/stories?ideology=progresismo&page_size=4&page=3").json()
    assert data["total_items"] == 10
    assert data["total_pages"] == 3
    assert data["page"] == 3
    assert len(data["items"]) == 2
    # Mismo orden que sin filtros: la más reciente primero
    assert "número 1" in data["items"][0]["title"]
    assert "número 0" in data["items"][1]["title"]


def test_filter_by_published_dates(client, db_session):
    _story_with(db_session, 1, published=datetime(2026, 7, 1, 12, 0, tzinfo=UTC))
    _story_with(db_session, 2, published=datetime(2026, 7, 2, 0, 0, tzinfo=UTC))
    _story_with(db_session, 3, published=datetime(2026, 7, 2, 23, 59, 59, 500000, tzinfo=UTC))
    _story_with(db_session, 4, published=datetime(2026, 7, 3, 0, 0, tzinfo=UTC))

    def count(query):
        return client.get(f"/api/v1/stories?{query}").json()["total_items"]

    # Solo fecha = día UTC completo, inclusivo en los dos extremos
    assert count("published_from=2026-07-02&published_to=2026-07-02") == 2
    assert count("published_from=2026-07-02") == 3
    assert count("published_to=2026-07-01") == 1
    # Rango invertido: vacío, no error
    assert count("published_from=2026-07-03&published_to=2026-07-01") == 0
    # Vacíos se ignoran
    assert count("published_from=&published_to=") == 4


def test_filter_by_published_instants(client, db_session):
    _story_with(db_session, 1, published=datetime(2026, 7, 1, 12, 0, tzinfo=UTC))
    _story_with(db_session, 2, published=datetime(2026, 7, 2, 0, 0, tzinfo=UTC))
    _story_with(db_session, 3, published=datetime(2026, 7, 2, 5, 0, tzinfo=UTC))

    def count(**params):
        return client.get("/api/v1/stories", params=params).json()["total_items"]

    # Instante exacto, inclusivo; la zona se respeta (19:00 en Bogotá = 00:00 UTC)
    assert count(published_from="2026-07-01T19:00:00-05:00") == 2
    assert count(published_to="2026-07-02T00:00:00Z") == 2
    assert count(published_to="2026-07-01T23:59:59.999Z") == 1
    assert count(published_from="2026-07-02T10:00:00+05:00") == 1
    # Sin zona se asume UTC
    assert count(published_from="2026-07-02T00:00:00") == 2
    # Un día local de Bogotá como lo envía el FE (medianoche a medianoche)
    assert count(
        published_from="2026-07-01T05:00:00.000Z", published_to="2026-07-02T04:59:59.999Z"
    ) == 2


def test_filter_by_published_validation(client, db_session):
    make_story(db_session, 1)
    for bad in ("ayer", "2026-13-01", "9999-12-31T23:00:00-05:00"):
        response = client.get("/api/v1/stories", params={"published_from": bad})
        assert response.status_code == 400, bad
        assert "published_from" in response.json()["detail"]
    assert client.get("/api/v1/stories?published_to=31/07/2026").status_code == 400


def test_filters_combined(client, db_session):
    july_2 = datetime(2026, 7, 2, 15, 0, tzinfo=UTC)
    target = _story_with(
        db_session, 1, label="soberanismo", published=july_2,
        source_category="regional", title="Consulta minera en el Cauca",
    )
    # Cada una falla en un solo filtro
    _story_with(db_session, 2, label="populismo", published=july_2,
                source_category="regional", title="Consulta minera en Nariño")
    _story_with(db_session, 3, label="soberanismo", published=july_2,
                source_category="nacional", title="Consulta minera en el Huila")
    _story_with(db_session, 4, label="soberanismo",
                published=datetime(2026, 6, 30, 15, 0, tzinfo=UTC),
                source_category="regional", title="Consulta minera en el Tolima")
    _story_with(db_session, 5, label="soberanismo", published=july_2,
                source_category="regional", title="Paro camionero en el Cauca")

    data = client.get(
        "/api/v1/stories",
        params={
            "ideology": ["soberanismo", "globalismo"],
            "source_category": "regional",
            "published_from": "2026-07-01",
            "published_to": "2026-07-31",
            "q": "consulta",
        },
    ).json()
    assert data["total_items"] == 1
    assert data["items"][0]["slug"] == target.slug
