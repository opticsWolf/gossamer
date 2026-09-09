//! Differential/corpus tests for the adapter kernels.


use super::common::*;
use super::finance::{coingecko_search_row_impl, eurostat_row_impl, py_int};
use super::legal::{
    courtlistener_row_impl, fed_doc_impl, govinfo_row_impl, oldp_snippet,
};
use super::misc::{nvd_row_impl, swh_origin_row_impl};
use super::scholar::{
    biorxiv_paper_impl, chemrxiv_join, chemrxiv_part, zenodo_names_impl,
};
use serde_json::Value;

use super::*;

#[test]
fn split_pair_shapes() {
    assert_eq!(
        frankfurter_split_pair_impl(Some("USD/EUR")).unwrap(),
        ("USD".to_string(), Some("EUR".to_string()))
    );
    assert_eq!(
        frankfurter_split_pair_impl(Some("usd eur")).unwrap(),
        ("USD".to_string(), Some("EUR".to_string()))
    );
    assert_eq!(
        frankfurter_split_pair_impl(Some("USD")).unwrap(),
        ("USD".to_string(), None)
    );
    assert!(frankfurter_split_pair_impl(None).is_err());
    assert!(frankfurter_split_pair_impl(Some("USDD")).is_err());
    assert!(frankfurter_split_pair_impl(Some("USD/EURO")).is_err());
}

#[test]
fn openmeteo_search_parses() {
    let body = r#"{"results": [{"name": "Berlin", "admin1": "Berlin", "country": "Germany", "latitude": 52.52, "longitude": 13.41}]}"#;
    let out = openmeteo_parse_search_impl(body, 5, "https://b").unwrap();
    assert_eq!(out.len(), 1);
    assert_eq!(out[0]["id"], "52.52,13.41");
    assert_eq!(out[0]["title"], "Berlin, Berlin, Germany");
}

#[test]
fn frankfurter_shapes() {
    let v2 = r#"[{"base": "USD", "quote": "EUR", "rate": 0.92, "date": "2024-01-01"}]"#;
    let out = frankfurter_parse_rates_impl(v2, "USD", None, 5).unwrap();
    assert_eq!(out[0]["id"], "USD/EUR");
    assert_eq!(out[0]["fields"]["rate"], 0.92);
    let map = r#"{"base": "USD", "quotes": {"EUR": 0.92, "JPY": 150.0}}"#;
    let out = frankfurter_parse_rates_impl(map, "USD", None, 5).unwrap();
    assert_eq!(out.len(), 2);
}

#[test]
fn nvd_route_shapes() {
    assert_eq!(
        nvd_route_query_impl("CVE-2021-44228"),
        ("cveId".to_string(), "CVE-2021-44228".to_string())
    );
    assert_eq!(
        nvd_route_query_impl("cve-2021-44228"),
        ("cveId".to_string(), "CVE-2021-44228".to_string())
    );
    // Python `$` matches before a trailing newline: routing only,
    // no stripping (the wrapper strips first).
    assert_eq!(
        nvd_route_query_impl("CVE-2021-44228\n").0,
        "cveId".to_string()
    );
    assert_eq!(
        nvd_route_query_impl(" log4shell ").0,
        "keywordSearch".to_string()
    );
}

#[test]
fn nvd_row_keeps_raw_id() {
    let cve: Value =
        serde_json::from_str(r#"{"id": 5, "published": "2021-12-10T00:00Z"}"#)
            .unwrap();
    let row = nvd_row_impl(&cve, "").unwrap();
    assert_eq!(row["id"], 5);
    assert_eq!(row["url"], "https://nvd.nist.gov/vuln/detail/5");
    assert_eq!(row["published"], "2021-12-10");
}

#[test]
fn nvd_fetch_empty() {
    for body in [
        r#"{}"#,
        r#"{"vulnerabilities": []}"#,
        r#"{"vulnerabilities": null}"#,
    ] {
        assert_eq!(nvd_parse_fetch_impl(body, "CVE-1").unwrap().len(), 0);
    }
    assert!(nvd_parse_fetch_impl(r#"{"vulnerabilities": [5]}"#, "").is_err());
}

#[test]
fn zenodo_names_join_error() {
    let people: Value =
        serde_json::from_str(r#"[{"name": 5}]"#).unwrap();
    assert_eq!(
        zenodo_names_impl(Some(&people)).unwrap_err(),
        "TypeError: sequence item 0: expected str instance, int found"
    );
    let people: Value =
        serde_json::from_str(r#"[{"name": "A"}, {"person_or_org": {"name": "B"}}]"#)
            .unwrap();
    assert_eq!(
        zenodo_names_impl(Some(&people)).unwrap(),
        "A, B".to_string()
    );
}

#[test]
fn legal_patent_shapes() {
    // CourtListener strips tags BEFORE slicing the snippet.
    let r: Value = serde_json::from_str(
        r#"{"caseName": 7, "cluster_id": 5}"#,
    )
    .unwrap();
    assert_eq!(
        courtlistener_row_impl(&r).unwrap_err(),
        "TypeError: expected string or bytes-like object, got 'int'"
    );
    // GovInfo download-link preference with package fallback.
    let r: Value = serde_json::from_str(
        r#"{"packageId": "P", "download": {"pdfLink": "https://pdf"}}"#,
    )
    .unwrap();
    let row = govinfo_row_impl(&r).unwrap();
    assert_eq!(row["url"], "https://pdf");
    assert_eq!(row["id"], "P");
    // PatentsView surfaces API errors as RuntimeError.
    assert_eq!(
        patentsview_parse_search_impl(r#"{"error": "bad key"}"#, 5)
            .unwrap_err(),
        "RuntimeError: PatentsView error: bad key"
    );
    // HUDOC search applies no result cap.
    let body = r#"{"results": [{"columns": {}}, {"columns": {}}]}"#;
    assert_eq!(hudoc_parse_search_impl(body).unwrap().len(), 2);
    // PatentsView fetch reads patents[0], then patent_number.
    let body = r#"{"patents": [{"patent_number": "3"}]}"#;
    let out = patentsview_parse_fetch_impl(body).unwrap();
    assert_eq!(out[0]["id"], "3");
    let out = patentsview_parse_fetch_impl(r#"{"other": 1}"#).unwrap();
    assert!(out.is_empty());
}

#[test]
fn oldp_fed_preprint_shapes() {
    // OLDP string snippets slice to chars, dicts raise KeyError.
    assert_eq!(
        oldp_snippet(Some(
            &serde_json::from_str(r#""abcdef""#).unwrap()
        ))
        .unwrap(),
        "a … b … c".to_string()
    );
    let d: Value = serde_json::from_str(r#"{"a": 1}"#).unwrap();
    assert_eq!(
        oldp_snippet(Some(&d)).unwrap_err(),
        "KeyError: slice(None, 3, None)"
    );
    // FederalRegister prefers html_url, then text_url; a truthy
    // non-dict agency raises at fields time (after the snippet).
    let d: Value =
        serde_json::from_str(r#"{"text_url": "https://text"}"#).unwrap();
    let row = fed_doc_impl(&d).unwrap();
    assert_eq!(row["url"], "https://text");
    assert_eq!(row["fields"]["agency"], "");
    let d: Value =
        serde_json::from_str(r#"{"agency": "EPA"}"#).unwrap();
    assert_eq!(
        fed_doc_impl(&d).unwrap_err(),
        "AttributeError: 'str' object has no attribute 'get'"
    );
    // ChemRxiv join reports the surviving (filtered) index.
    let items: Value =
        serde_json::from_str(r#"[{"name": ""}, {"name": 5}]"#).unwrap();
    let arr = items.as_array().unwrap();
    assert_eq!(
        chemrxiv_join(arr.iter().map(chemrxiv_part).collect(), true)
            .unwrap_err(),
        "TypeError: sequence item 0: expected str instance, int found"
    );
    // BioRxiv threads the server into flat fields.
    let p: Value =
        serde_json::from_str(r#"{"doi": "10.1/x"}"#).unwrap();
    let row = biorxiv_paper_impl(&p, "medrxiv").unwrap();
    assert_eq!(row["fields"]["server"], "medrxiv");
    assert_eq!(row["url"], "https://www.biorxiv.org/content/10.1/x");
}

#[test]
fn financial_shapes() {
    // Eurostat `int()` keys: underscores and signs parse, hex does not.
    assert_eq!(py_int("1_0"), Some(10));
    assert_eq!(py_int("  +12  "), Some(12));
    assert_eq!(py_int("0x1A"), None);
    assert_eq!(py_int("_1"), None);
    // Eurostat id joins are strict (non-string codes raise).
    let coords = vec![(Value::String("g".to_string()), Value::from(5), Value::Null)];
    assert_eq!(
        eurostat_row_impl("c", "L", &coords, &Value::Null).unwrap_err(),
        "TypeError: sequence item 0: expected str instance, int found"
    );
    // CoinGecko `.upper()` runs on the raw symbol value.
    let coin: Value =
        serde_json::from_str(r#"{"symbol": 5}"#).unwrap();
    assert_eq!(
        coingecko_search_row_impl(&coin).unwrap_err(),
        "AttributeError: 'int' object has no attribute 'upper'"
    );
    // AlphaVantage note rows take the whole-body raw Python-side;
    // the kernel marks them with a Null meta.
    let (rec, meta) =
        alphavantage_parse_fetch_impl(r#"{"notes": "slow"}"#, "IBM").unwrap();
    assert_eq!(rec["id"], "IBM");
    assert_eq!(rec["title"], "slow");
    assert!(meta.is_null());
}

#[test]
fn subscript_key_shapes() {
    let d: Value = serde_json::from_str(r#"{"a": 1}"#).unwrap();
    assert_eq!(
        subscript_hits(Some(&d), 5).unwrap_err(),
        "KeyError: slice(None, 5, None)"
    );
    let n = Value::Null;
    assert!(subscript_hits(Some(&n), 5).is_err());
    assert_eq!(subscript_hits(None, 5).unwrap().len(), 0);
}

#[test]
fn batch9_register_shapes() {
    // eCFR: zero-stripped two-pass DFS.
    let tree = r#"{"children": [{"identifier": "0113", "label": "P", "type": "appendix"}, {"identifier": "113", "label": "Q", "type": "part", "children": [{"identifier": "s", "label": "S", "type": "section"}]}]}"#;
    let node = ecfr_find_part_impl(tree, "113").unwrap().unwrap();
    assert_eq!(node["label"], "Q");
    let rec = ecfr_part_record_impl(&serde_json::to_string(&node).unwrap(), "21", "113").unwrap();
    assert_eq!(rec["snippet"], "Sections: s S");
    assert_eq!(rec["fields"]["section_count"], 1);
    let rec = ecfr_title_record_impl(tree, "21").unwrap();
    assert_eq!(rec["id"], "21");
    // Bundesbank: keep-previous period default + limit break.
    let out = bundesbank_parse_impl("<Obs><ObsValue value='v'/></Obs>", "F", "K", 5).unwrap();
    assert_eq!(out[0]["fields"]["date"], "");
    // BIS: eager double-pop (TIME_PERIOD wins, TIME still consumed).
    let out = bis_parse_impl("<Series F='1'><Obs TIME='a' TIME_PERIOD='b' OBS_VALUE='v'/></Series>", "F", "K", 5).unwrap();
    assert_eq!(out[0]["fields"]["date"], "b");
    assert_eq!(out[0]["raw"]["extra"], serde_json::json!({}));
    // EPO: last document-id wins; error fetch is null.
    let out = epo_parse_search_impl("<exchange-document><document-id document-id-type='epodoc'><doc-number>N</doc-number><kind>A</kind></document-id></exchange-document>", 5).unwrap();
    assert_eq!(out[0]["id"], "NA");
    assert!(epo_parse_fetch_impl("<root/>").unwrap().is_null());
    // KIPRIS: duplicate tags keep first position, last value.
    let out = kipris_parse_search_impl("<item><a>1</a><a>2</a></item>", 5).unwrap();
    assert_eq!(out[0]["raw"]["a"], "2");
}

#[test]
fn batch8_xml_shapes() {
    // xmlatom: local names ignore prefixes; tails invisible.
    let root = crate::xmlatom::parse_document(
        "<f xmlns:a='u'><a:x>y<b/>tail</a:x></f>").unwrap();
    let x = root.child("x").unwrap();
    assert_eq!(x.text, "y");
    // arxiv: doi/category fallback chains + append-then-break.
    let out = arxiv_parse_search_impl(
        "<feed><entry><id>http://arxiv.org/abs/1</id>\
         <link title='doi' href='https://doi.org/10.9/y'/>\
         <category term='t' scheme='http://arxiv.org/schemas/atom'/>\
         </entry></feed>", 0).unwrap();
    assert_eq!(out.len(), 1);
    assert_eq!(out[0]["doi"], "10.9/y");
    assert_eq!(out[0]["fields"]["arxiv"]["primary_category"], "t");
    // arxiv fetch: error record shape.
    let rec = arxiv_parse_fetch_impl(
        "<feed/>", "9", &Value::String("R".to_string())).unwrap();
    assert_eq!(rec["snippet"], "");
    assert_eq!(rec["url"], "R");
    // pubmed: char-sliced string idlists + direct-index errors.
    let out = pubmed_parse_search_impl(
        r#"{"esearchresult": {"idlist": "ab"}}"#, 5).unwrap();
    assert_eq!(out.len(), 2);
    assert!(pubmed_parse_search_impl("{}", 5).is_err());
    // pubmed fetch: None for article-less payloads.
    assert!(pubmed_parse_fetch_impl("<PubmedArticleSet/>", "1")
        .unwrap()
        .is_none());
}

#[test]
fn batch7_misc_shapes() {
    // WorldBank: indicator fallback + recent join slice.
    let body = r#"[{"page": 1}, [{"indicator": {"id": "X", "value": "V"}, "date": "2023", "value": 5}]]"#;
    let out = worldbank_parse_fetch_impl(body, &Value::String("R".to_string()), "D/R").unwrap();
    assert_eq!(out["id"], "X");
    assert_eq!(out["snippet"], "1 observations; recent: 2023:5");
    // FRED CSV: header skipped, raw window kept.
    let out = fred_parse_csv_impl("DATE,VALUE\n2024-01-01,1.5\n", &Value::String("GDP".to_string())).unwrap();
    assert_eq!(out["id"], "GDP");
    assert_eq!(out["raw"], "DATE,VALUE\n2024-01-01,1.5");
    // GitHub: str() id + url chain.
    let body = r#"{"items": [{"id": 7, "html_url": null, "url": "U"}]}"#;
    let out = github_parse_search_impl(body, 5).unwrap();
    assert_eq!(out[0]["id"], "7");
    assert_eq!(out[0]["url"], "U");
    // Congress: em-dash snippet.
    let body = r#"{"results": [{"cgi_id": "C", "title": "Sen", "party": "D", "state": "CA", "district": "1"}]}"#;
    let out = congress_parse_search_impl(body, 5).unwrap();
    assert_eq!(out[0]["snippet"], "Sen D \u{2014} CA1");
    // NASA: fetch ignores hostile close_approach_data.
    let body = r#"{"a": 1, "close_approach_data": 5, "estimated_diameter": {"meters": {"estimated_diameter_max": 2}}}"#;
    assert!(nasa_parse_fetch_impl(body, &Value::String("R".to_string())).is_ok());
    // SWH: falsy url falls back to visits; sid shape.
    let out = swh_origin_row_impl(&serde_json::from_str(r#"{"visit_types": []}"#).unwrap(), "fb").unwrap();
    assert_eq!(out["url"], "https://archive.softwareheritage.org/browse/origin/?origin_url=fb");
    let out = swh_parse_fetch_sid_impl(r#"{"id": "s:1"}"#, "s:1").unwrap();
    assert_eq!(out["url"], "https://archive.softwareheritage.org/s:1");
    // Overpass: name-or-type:id title + 6-tag cap.
    let body = r#"{"elements": [{"type": "node", "id": 3, "tags": {"a": "1", "b": "2", "c": "3", "d": "4", "e": "5", "f": "6", "g": "7"}}]}"#;
    let out = overpass_parse_search_impl(body, 5).unwrap();
    assert_eq!(out[0]["title"], "node:3");
    assert_eq!(out[0]["snippet"], "a=1, b=2, c=3, d=4, e=5, f=6");
    // Census: header lowering + raw payload shape ("06" sits
    // under `st_ate`, so the state-keyed id folds to "").
    let body = r#"[["ST ATE"], ["06"]]"#;
    let out = census_parse_search_impl(body, "D", 5).unwrap();
    assert_eq!(out[0]["id"], "");
    assert!(out[0].get("raw").is_some());
}

#[test]
fn batch6_scholarly_shapes() {
    // OpenAlex: author folding + url/doi asymmetry.
    let body = r#"{"results": [{"id": "W1", "title": "", "doi": null, "authorships": [{"author": {"display_name": "A."}}, {"author": {}}]}]}"#;
    let out = openalex_parse_search_impl(body, 5).unwrap();
    assert_eq!(out[0]["title"], "");
    assert_eq!(out[0]["url"], "W1");
    assert_eq!(out[0]["authors"], "A.");
    assert_eq!(out[0]["snippet"], "A.");
    // Crossref: title-first-char, date-parts head, abstract head.
    let body = r#"{"message": {"items": [{"DOI": "10.1/x", "title": ["Ab"], "URL": "U", "published": {"date-parts": [[2024]]}, "author": [{"family": "F"}], "abstract": "Abc"}]}}"#;
    let out = crossref_parse_search_impl(body, 5).unwrap();
    assert_eq!(out[0]["title"], "Ab");
    assert_eq!(out[0]["published"], serde_json::json!([2024]));
    assert_eq!(out[0]["snippet"], "Abc");
    // Crossref fetch: direct message indexing + DOI fallback.
    assert!(crossref_parse_fetch_impl("{}", &Value::Null).is_err());
    let out = crossref_parse_fetch_impl(
        r#"{"message": {}}"#, &Value::String("10.1/f".to_string())).unwrap();
    assert_eq!(out["id"], "10.1/f");
    // OpenLibrary: unconditional fetch URL vs guarded search URL.
    let body = r#"{"docs": [{"title": "T", "author": ["A."], "isbn": ["1"]}]}"#;
    let out = openlibrary_parse_search_impl(body, 5, "https://b").unwrap();
    assert_eq!(out[0]["url"], "");
    let out =
        openlibrary_parse_fetch_impl("{}", "/books/K", "https://b").unwrap();
    assert_eq!(out["url"], "https://b/books/K");
    // DOAJ: doi hunt skips non-doi identifiers, missing id reads None.
    let body = r#"{"results": [{"id": "r", "bibjson": {"identifier": [{"type": "issn", "id": "1"}, {"type": "doi"}]}}]}"#;
    let out = doaj_parse_search_impl(body, 5).unwrap();
    assert_eq!(out[0]["doi"], Value::Null);
}

#[test]
fn version_gated_error_spellings() {
    // `d[slice]`: TypeError through 3.11, KeyError from 3.12.
    assert_eq!(dict_slice_error(5, Some(10)), "TypeError: unhashable type: 'slice'");
    assert_eq!(dict_slice_error(5, Some(11)), "TypeError: unhashable type: 'slice'");
    assert_eq!(dict_slice_error(5, Some(12)), "KeyError: slice(None, 5, None)");
    assert_eq!(dict_slice_error(5, Some(13)), "KeyError: slice(None, 5, None)");
    assert_eq!(dict_slice_error(5, None), "KeyError: slice(None, 5, None)");
    // `re.sub` suffix added in 3.11.
    assert_eq!(re_sub_type_error("int", Some(10)), "TypeError: expected string or bytes-like object");
    assert_eq!(re_sub_type_error("int", Some(11)), "TypeError: expected string or bytes-like object, got 'int'");
    assert_eq!(re_sub_type_error("int", None), "TypeError: expected string or bytes-like object, got 'int'");
    // `s[str]` suffix added in 3.11.
    assert_eq!(str_subscript_error(Some(10)), "TypeError: string indices must be integers");
    assert_eq!(str_subscript_error(Some(11)), "TypeError: string indices must be integers, not 'str'");
    assert_eq!(str_subscript_error(None), "TypeError: string indices must be integers, not 'str'");
}
