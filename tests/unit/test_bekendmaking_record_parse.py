"""SRU record -> row dict, with attributes.

`_parse` used to run a regex over the record text, under a comment claiming "nothing in the
record is silently discarded". It could not keep that promise by construction: `[^>]*>` ate
every attribute and `([^<]+)` matched only leaf text. These cases pin the two things the
regex could not do and the three it must keep doing, so the claim is now checked rather than
asserted.

The fixture is the real shape of gmb-2026-341525 (Diemen, 2026-07-16), trimmed: the same
namespace prefixes declared on the same envelope root, `itemUrl` six times with six different
`manifestation` values, and `gebiedsmarkering` as a container holding an RD New polygon.
"""
import json
import xml.etree.ElementTree as ET

from scripts.import_bekendmaking import _collect, _page, _parse, _tag

ENVELOPE = """<sru:searchRetrieveResponse
    xmlns:sru="http://docs.oasis-open.org/ns/search-ws/sruResponse"
    xmlns:gzd="http://standaarden.overheid.nl/sru"
    xmlns:dcterms="http://purl.org/dc/terms/"
    xmlns:overheid="http://standaarden.overheid.nl/owms/terms/"
    xmlns:overheidwetgeving="http://standaarden.overheid.nl/wetgeving/terms/">
  <sru:numberOfRecords>1</sru:numberOfRecords>
  <sru:records><sru:record><sru:recordData><gzd:gzd><gzd:originalData>
    <overheid:meta><overheid:owmskern>
      <dcterms:identifier>gmb-2026-341525</dcterms:identifier>
      <dcterms:title>Aanvraag Exploitatievergunning Dalsteindreef 7033</dcterms:title>
      <dcterms:type scheme="OVERHEIDop.Rubriek">exploitatievergunning</dcterms:type>
      <dcterms:date>2026-07-16</dcterms:date>
      <dcterms:creator scheme="OVERHEID.Gemeente">Diemen</dcterms:creator>
      <dcterms:publisher scheme="OVERHEID.Gemeente">Diemen</dcterms:publisher>
      <dcterms:language>nl</dcterms:language>
      <dcterms:hasVersion
        resourceIdentifier="https://zoek.officielebekendmakingen.nl/gmb-2026-341525.html">1</dcterms:hasVersion>
    </overheid:owmskern><overheid:owmsmantel>
      <overheid:ligtInGemeente>Diemen</overheid:ligtInGemeente>
      <overheidwetgeving:gebiedsmarkering scheme="OVERHEIDop.locatietype">
        <overheidwetgeving:Vlak>
          <overheidwetgeving:geometrie>POLYGON ((125635.4 482503.5, 125683.3 482418.3, 125635.4 482503.5))</overheidwetgeving:geometrie>
        </overheidwetgeving:Vlak>
      </overheidwetgeving:gebiedsmarkering>
      <overheidwetgeving:locatiegebied>POLYGON((4.9564284 52.329684,4.9571385 52.328921,4.9564284 52.329684))</overheidwetgeving:locatiegebied>
      <overheidwetgeving:geometrielabel>Dalsteindreef 7033, 1112XJ Diemen</overheidwetgeving:geometrielabel>
    </overheid:owmsmantel></overheid:meta>
    <gzd:itemUrl manifestation="html">https://repository.overheid.nl/x/html</gzd:itemUrl>
    <gzd:itemUrl manifestation="metadata">https://repository.overheid.nl/x/metadata</gzd:itemUrl>
    <gzd:itemUrl manifestation="metadataowms">https://repository.overheid.nl/x/owms</gzd:itemUrl>
    <gzd:itemUrl manifestation="odt">https://repository.overheid.nl/x/odt</gzd:itemUrl>
    <gzd:itemUrl manifestation="pdf">https://repository.overheid.nl/x/pdf</gzd:itemUrl>
    <gzd:itemUrl manifestation="xml">https://repository.overheid.nl/x/xml</gzd:itemUrl>
    <gzd:preferredUrl>https://zoek.officielebekendmakingen.nl/gmb-2026-341525.html</gzd:preferredUrl>
  </gzd:originalData></gzd:gzd></sru:recordData></sru:record></sru:records>
</sru:searchRetrieveResponse>"""


def record() -> ET.Element:
    return ET.fromstring(ENVELOPE).find(".//{*}recordData")


def raw_of(rec: ET.Element) -> dict:
    return json.loads(_parse(rec)["raw"])


# ── what the regex could not see ─────────────────────────────────────────────

def test_attributes_are_kept_even_on_a_promoted_tag():
    """`type` is promoted to a column; its `scheme` is in no column, so it belongs in raw."""
    raw = raw_of(record())
    assert raw["type@scheme"] == ["OVERHEIDop.Rubriek"]
    assert raw["creator@scheme"] == ["OVERHEID.Gemeente"]


def test_hasversion_keeps_the_url_that_lives_in_the_attribute():
    # The element's TEXT is the version number; the document URL is the attribute. Storing
    # only the text is how `"hasVersion": ["1"]` came to be the whole of it.
    raw = raw_of(record())
    assert raw["hasVersion"] == ["1"]
    assert raw["hasVersion@resourceIdentifier"] == [
        "https://zoek.officielebekendmakingen.nl/gmb-2026-341525.html"]


def test_a_container_element_contributes_its_attribute_not_its_children():
    """`gebiedsmarkering` has no text of its own, so only its scheme is recorded.

    Its nested polygon must NOT leak in: it is the RD New copy of a shape
    bekendmaking_locatie already holds in WGS84, and a second copy is a kilobyte a row.
    """
    raw = raw_of(record())
    assert raw["gebiedsmarkering@scheme"] == ["OVERHEIDop.locatietype"]
    assert "gebiedsmarkering" not in raw
    assert "geometrie" not in raw and "locatiegebied" not in raw


def test_a_promoted_tag_that_repeats_is_kept_in_full():
    """itemUrl is in _PROMOTED, no column reads it, and it occurs six times.

    Under the old rule it was excluded from raw as "promoted" and promoted nowhere, so all
    six URLs and the manifestations telling them apart were lost together.
    """
    raw = raw_of(record())
    assert len(raw["itemUrl"]) == 6
    assert raw["itemUrl@manifestation"] == [
        "html", "metadata", "metadataowms", "odt", "pdf", "xml"]
    # Positionally paired, so a consumer can find the xml manifestation's URL.
    pair = dict(zip(raw["itemUrl@manifestation"], raw["itemUrl"]))
    assert pair["xml"].endswith("/xml")


# ── what must keep working ───────────────────────────────────────────────────

def test_promoted_single_valued_tags_stay_out_of_raw():
    raw = raw_of(record())
    for t in ("identifier", "title", "date", "ligtInGemeente", "geometrielabel"):
        assert t not in raw, f"{t} is in a column and should not be duplicated in raw"


def test_columns_are_unchanged_by_the_parser_switch():
    r = _parse(record())
    assert r["identifier"] == "gmb-2026-341525"
    assert r["doc_type"] == "exploitatievergunning"
    assert r["published_at"].isoformat() == "2026-07-16"
    assert r["authority"] == "Diemen"
    assert r["gemeente"] == "Diemen"
    assert r["label"] == "Dalsteindreef 7033, 1112XJ Diemen"
    assert r["url"] == "https://zoek.officielebekendmakingen.nl/gmb-2026-341525.html"
    assert r["activiteit"] == []
    # The WGS84 shape, not the RD New one. Reading `geometrie` instead would put metres
    # into a geometry(...,4326) column and PostGIS would accept it without complaint.
    assert len(r["shapes"]) == 1
    assert r["shapes"][0].startswith("POLYGON((4.95")


def test_a_record_missing_a_required_field_is_dropped_not_half_written():
    rec = record()
    for el in list(rec.iter()):
        for child in list(el):
            if _tag(child) == "title":
                el.remove(child)
    assert _parse(rec) is None


def test_namespace_prefixes_collapse_to_the_local_name():
    # Verified over 50 Amsterdam records: 55 local names, none under two namespace URIs.
    el = ET.fromstring('<a xmlns="http://example.org/ns">x</a>')
    assert _tag(el) == "a"


def test_xmlns_declarations_never_become_raw_keys():
    raw = raw_of(record())
    assert not [k for k in raw if "xmlns" in k]


def test_collect_is_a_pure_function_of_the_element():
    rec = record()
    assert _collect(rec) == _collect(rec)


def test_page_is_the_entry_point_that_needs_the_whole_envelope():
    """A <recordData> substring is not well-formed on its own -- its prefixes are declared
    on the envelope root. The regex `_page` used to cut records out of the response text,
    which is why `_parse` could never have been handed a parseable fragment."""
    inner = ENVELOPE.split("<sru:recordData>", 1)[1].split("</sru:recordData>", 1)[0]
    try:
        ET.fromstring(inner)
    except ET.ParseError as e:
        assert "unbound prefix" in str(e)
    else:
        raise AssertionError("the fragment parsed; the fixture no longer pins the reason")
    assert callable(_page)
