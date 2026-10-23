"""Hidden session metrics: personal best per user, overall best and a top 5 ranking."""


class TestSessionMetrics:
    async def test_keeps_only_the_better_value(self, client):
        first = await client.put("/ui/metrics/session", json={"value": 120})
        assert first.status_code == 200, first.text
        assert first.json()["personal"] == 120

        lower = await client.put("/ui/metrics/session", json={"value": 80})
        assert lower.json()["personal"] == 120

        higher = await client.put("/ui/metrics/session", json={"value": 300})
        assert higher.json()["personal"] == 300
        assert higher.json()["best"]["value"] == 300

    async def test_summary_and_ranking(self, client):
        await client.put("/ui/metrics/session", json={"value": 50})

        summary = await client.get("/ui/metrics/session")
        assert summary.status_code == 200
        assert summary.json()["personal"] == 50

        ranking = await client.get("/ui/metrics/ranking")
        assert ranking.status_code == 200
        entries = ranking.json()
        assert 1 <= len(entries) <= 5
        assert entries[0]["value"] == 50
        assert "@" not in entries[0]["name"]

    async def test_rejects_invalid_values(self, client):
        assert (await client.put("/ui/metrics/session", json={"value": -1})).status_code == 400
        assert (await client.put("/ui/metrics/session", json={"value": 10**9})).status_code == 400

    async def test_is_not_part_of_the_openapi_schema(self, client):
        schema = (await client.get("/openapi.json")).json()
        assert not any(path.startswith("/ui/metrics") for path in schema["paths"])
