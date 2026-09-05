//! Domain routing (port of `gossamer.research_categories.classify`).
//!
//! Ports the scoring kernel only: keyword tables, word-boundary matching,
//! distinct-hit counting, table-order tie-break, `general` fallback. The
//! `Category` objects, descriptions, provider lists, and adapter
//! factories stay Python (they feed the taxonomy JSON and imports);
//! `classify()` delegates here and re-resolves the name.
//!
//! Table-drift guard: `tests/test_rust_parity_categories.py` classifies
//! every Python keyword in isolation through both implementations, so a
//! table edit on either side fails loudly.
//!
//! Pinned by `tests/test_rust_parity_categories.py`.

use pyo3::prelude::*;
use regex::Regex;
use std::sync::OnceLock;

struct CategoryTable {
    name: &'static str,
    keywords: &'static [&'static str],
}

// Order matters: tie-break for equal hit counts ( scholarly > legal >
// patent > financial > geo ), mirroring CATEGORIES.
static TABLES: &[CategoryTable] = &[
    CategoryTable {
        name: "scholarly",
        keywords: &[
            "paper", "papers", "citation", "citations", "journal",
            "peer-reviewed", "peer reviewed", "arxiv", "e-print", "doi",
            "scholar", "academic", "academia", "publication", "publications",
            "research paper", "professor", "university", "thesis", "theses",
            "abstract", "semanticscholar", "refereed", "conference paper",
            "open access",
        ],
    },
    CategoryTable {
        name: "legal",
        keywords: &[
            "case law", "statute", "statutes", "regulation", "regulations",
            "code of federal regulations", "cfr", "congress bill", "bill",
            "eur-lex", "eu law", "court", "legislation", "legislature",
            "federal register", "ordinance", "precedent", "appeal",
            "supreme court", "government code",
            "echr", "hudoc", "egmr", "menschenrechte", "eugh", "cjeu",
            "celex", "ecli", "gdpr", "dsgvo", "bverfg",
            "bundesverfassungsgericht", "bgh", "bundesgerichtshof",
            "rechtsprechung", "urteil", "urteile", "aktenzeichen",
        ],
    },
    CategoryTable {
        name: "patent",
        keywords: &[
            "patent", "patents", "patented", "patentability",
            "patent application", "patent search", "prior art",
            "uspto", "epo", "espacenet", "jpo", "kipo", "kipris",
            "cnipa", "dpma", "depatisnet", "pct application", "patent office",
        ],
    },
    CategoryTable {
        name: "financial",
        keywords: &[
            "stock", "quote", "quotes", "finance", "financial", "market",
            "exchange rate", "index", "indices", "share", "shares", "trading",
            "bull market", "bear market", "portfolio", "dividend", "ticker",
            "cryptocurrency", "crypto",
            "bundesbank", "ecb", "ezb", "eurostat", "hicp", "hvpi",
            "leitzins", "leitzinsen", "geldpolitik", "zinssatz",
            "euribor", "eonia", "estr", "eurozone", "euro-zone",
            "euro area", "euroraum", "bip", "staatsverschuldung",
            "staatsschulden", "arbeitslosenquote",
            "aktie", "aktien", "aktienkurs", "dax", "dividende",
        ],
    },
    CategoryTable {
        name: "geo",
        keywords: &[
            "weather", "climate", "temperature", "forecast", "forecasts",
            "coordinates", "coordinate", "latitude", "longitude", "geocod",
            "hurricane", "tropical storm", "heat wave", "cold snap",
            "open-meteo", "place name", "zip code", "zipcode", "postal code",
            "rainfall", "snowfall",
        ],
    },
];

static PATTERNS: OnceLock<Vec<Vec<Regex>>> = OnceLock::new();

fn patterns() -> &'static Vec<Vec<Regex>> {
    PATTERNS.get_or_init(|| {
        TABLES
            .iter()
            .map(|t| {
                t.keywords
                    .iter()
                    .map(|kw| {
                        Regex::new(&format!(r"\b{}\b", regex::escape(kw)))
                            .expect("category keyword pattern must compile")
                    })
                    .collect()
            })
            .collect()
    })
}

pub fn classify_query_impl(query: Option<&str>) -> String {
    let text = query.unwrap_or("").to_lowercase();
    let pats = patterns();
    let mut best = "general";
    let mut best_score = 0usize;
    for (table, regexes) in TABLES.iter().zip(pats.iter()) {
        let score = regexes.iter().filter(|re| re.is_match(&text)).count();
        if score > best_score {
            best = table.name;
            best_score = score;
        }
    }
    best.to_string()
}

#[pyfunction]
#[pyo3(signature = (query = None))]
pub fn classify_query(query: Option<&str>) -> String {
    classify_query_impl(query)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn single_topic_routes() {
        assert_eq!(classify_query_impl(Some("quantum patent prior art")), "patent");
        assert_eq!(classify_query_impl(Some("BVerfG urteil")), "legal");
        assert_eq!(classify_query_impl(Some("dax aktienkurs")), "financial");
        assert_eq!(classify_query_impl(Some("weather berlin")), "geo");
        assert_eq!(classify_query_impl(Some("arxiv paper doi")), "scholarly");
    }

    #[test]
    fn fallback_and_ties() {
        assert_eq!(classify_query_impl(None), "general");
        assert_eq!(classify_query_impl(Some("")), "general");
        assert_eq!(classify_query_impl(Some("hello world")), "general");
        // Tie keeps table order: legal precedes patent.
        assert_eq!(classify_query_impl(Some("BVerfG patent case")), "legal");
    }
}
