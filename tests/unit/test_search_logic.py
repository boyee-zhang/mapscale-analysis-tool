from api.main.routers.search import _build_es_query, _pdok_relevant


class TestBuildEsQuery:
    def test_default_weights(self):
        q = _build_es_query("Kerkstraat Amsterdam", 8, 3.0, 2.0, 5.0, 1.0)
        multi = q["query"]["bool"]["should"][0]["multi_match"]
        assert "street^3.0" in multi["fields"]
        assert "city^2.0" in multi["fields"]
        assert multi["fuzziness"] == "AUTO"
        assert multi["minimum_should_match"] == "75%"

    def test_custom_weights(self):
        q = _build_es_query("1112ZH", 5, 1.0, 1.0, 8.0, 1.0)
        term = q["query"]["bool"]["should"][1]["term"]["postcode"]
        assert term["boost"] == 8.0
        assert term["value"] == "1112ZH"

    def test_postcode_normalised(self):
        q = _build_es_query("1112 zh", 5, 3.0, 2.0, 5.0, 1.0)
        term = q["query"]["bool"]["should"][1]["term"]["postcode"]
        assert term["value"] == "1112ZH"

    def test_size_applied(self):
        q = _build_es_query("test", 3, 3.0, 2.0, 5.0, 1.0)
        assert q["size"] == 3


class TestPdokRelevant:
    def _make_results(self, labels: list[str]) -> list[dict]:
        return [{"label": l} for l in labels]

    def test_relevant_when_all_tokens_match(self):
        results = self._make_results(["Kerkstraat 10, Amsterdam"])
        assert _pdok_relevant("Kerkstraat Amsterdam", results) is True

    def test_irrelevant_when_key_token_missing(self):
        # "OurCampus" not in any label — only "Diemen" matches
        results = self._make_results([
            "Dalsteindreef 1, Diemen",
            "Weesperstraat 5, Diemen",
        ])
        assert _pdok_relevant("OurCampus Diemen", results) is False

    def test_single_token_query(self):
        results = self._make_results(["Hoofdstraat 1, Utrecht"])
        assert _pdok_relevant("Hoofdstraat", results) is True

    def test_short_tokens_ignored(self):
        # "Jan" (len=3) and "699" (len=3) are filtered — only "Wolkerslaan" counts
        results = self._make_results(["Jan Wolkerslaan 699, Diemen"])
        assert _pdok_relevant("Jan Wolkerslaan 699", results) is True

    def test_empty_results(self):
        assert _pdok_relevant("Kerkstraat", []) is False
