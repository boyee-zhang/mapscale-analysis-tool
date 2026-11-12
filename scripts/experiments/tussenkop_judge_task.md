# Task: label every `tussenkop` heading in your shard

## The question each unit is judged against (verbatim)

"If this heading were dropped from the stored body text, would any information be lost that is
not already present in the surrounding `<al>` paragraphs of the same document?"

Background you need, and nothing more: these are Dutch municipal publications
(officiële bekendmakingen). An importer currently stores only the text of `<al>` elements and
silently discards every `<tussenkop>` (heading). The caller is deciding whether recovering those
headings is worth re-fetching 94,183 documents. You are judging whether the discarded headings
carry information. You are NOT deciding whether to re-fetch.

## Input shape

Your input file is JSON: `{doc_type, n_docs, n_headings, docs: [...]}`. Each entry in `docs` is

```
{ "identifier": "gmb-2026-...", "url": "...", "n_tussenkop": N, "n_al": M,
  "sequence": [ {"el": "tussenkop"|"al", "text": "...", "nested_in": null|"al"|"tussenkop"}, ... ] }
```

`sequence` is in document order. **A unit is one element of `sequence` whose `el` is
`"tussenkop"`.** Judge it against the `al` elements around it in that same `sequence` — the ones
that follow it up to the next `tussenkop` are its section, and the ones before it are context.

Total units in your shard = that file's `n_headings`. Every one gets a record.

`unit_id` is `"<identifier>#<zero-based index of the element in sequence>"`, e.g.
`"gmb-2026-502043#4"`.

## Labels — use exactly these four

- **`boilerplate_procedural`** — the heading names a generic legal/procedural section (objection,
  appeal, inspection, viewing period, contact, publication rules — Dutch: *Bezwaar*, *Beroep*,
  *Zienswijze*, *Inzage*, *Ter inzage*, *Rechtsmiddelen*, *Informatie*, *Contact* and the like),
  and the section under it is standard wording that would read the same in any publication of
  this type. It says nothing about this particular case.
- **`navigational_duplicate`** — the heading is a structural label whose information appears, in
  full, in the `al` paragraph(s) immediately under it. Dropping the heading loses nothing because
  the same fact is in the body text. Example shape: a heading `Omschrijving` above a paragraph
  that itself begins `Omschrijving: kappen van 6 bomen...`.
- **`substantive_unique`** — the heading itself carries a concrete fact about THIS document — an
  address, a project or applicant name, a date, a case number, a decision, a street, a
  measure — **and that fact does not appear in the `al` paragraphs of the same document**. This
  is the only label that means real information is being lost. Check the whole `sequence` before
  you assign it, not just the next paragraph.
- **`unclear`** — you cannot decide. Say in `reason` what would make it decidable.

## Deciding between `navigational_duplicate` and `substantive_unique`

This distinction is the entire point of the exercise, so spend your effort here. The test is not
whether the heading looks informative — it is whether the information is **also** in the `al`
text of the same document. A heading reading `Zaakadres: Hoofdstraat 12` is
`navigational_duplicate` if some `al` in that document also gives that address, and
`substantive_unique` if none does. When a heading is an address or a name and you cannot find it
in any `al` of that document, that is `substantive_unique` — that is exactly the loss the caller
is trying to measure.

Quote in `evidence` both the heading and the decisive `al` text (or state that no `al` contains
it), so the caller can check you without re-reading the source.
