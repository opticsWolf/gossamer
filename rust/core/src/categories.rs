//! Domain routing for research queries.
//!
//! Pure port of the scoring kernel in legacy `src/categories.rs`. Category metadata, provider
//! lists, and adapter factories stay in Python; this module only returns a category name.

use std::sync::OnceLock;

use regex::Regex;

struct CategoryTable {
    name: &'static str,
    keywords: &'static [&'static str],
}

// Order matters: tie-break for equal hit counts mirrors Python CATEGORIES.
static TABLES: &[CategoryTable] = &[
    CategoryTable {
        name: "scholarly",
        keywords: &[
            "paper",
            "papers",
            "citation",
            "citations",
            "journal",
            "peer-reviewed",
            "peer reviewed",
            "arxiv",
            "e-print",
            "doi",
            "scholar",
            "academic",
            "academia",
            "publication",
            "publications",
            "research paper",
            "professor",
            "university",
            "thesis",
            "theses",
            "abstract",
            "semanticscholar",
            "refereed",
            "conference paper",
            "open access",
            "huggingface",
            "hugging face",
            "model card",
            "daily papers",
        ],
    },
    CategoryTable {
        name: "legal",
        keywords: &[
            "case law",
            "statute",
            "statutes",
            "regulation",
            "regulations",
            "code of federal regulations",
            "cfr",
            "congress bill",
            "bill",
            "eur-lex",
            "eu law",
            "court",
            "legislation",
            "legislature",
            "federal register",
            "ordinance",
            "precedent",
            "appeal",
            "supreme court",
            "government code",
            "echr",
            "hudoc",
            "egmr",
            "menschenrechte",
            "eugh",
            "cjeu",
            "celex",
            "ecli",
            "gdpr",
            "dsgvo",
            "bverfg",
            "bundesverfassungsgericht",
            "bgh",
            "bundesgerichtshof",
            "rechtsprechung",
            "urteil",
            "urteile",
            "aktenzeichen",
        ],
    },
    CategoryTable {
        name: "patent",
        keywords: &[
            "patent",
            "patents",
            "patented",
            "patentability",
            "patent application",
            "patent search",
            "prior art",
            "uspto",
            "epo",
            "espacenet",
            "jpo",
            "kipo",
            "kipris",
            "cnipa",
            "dpma",
            "depatisnet",
            "pct application",
            "patent office",
        ],
    },
    CategoryTable {
        name: "financial",
        keywords: &[
            "stock",
            "quote",
            "quotes",
            "finance",
            "financial",
            "market",
            "exchange rate",
            "index",
            "indices",
            "share",
            "shares",
            "trading",
            "bull market",
            "bear market",
            "portfolio",
            "dividend",
            "ticker",
            "cryptocurrency",
            "crypto",
            "bundesbank",
            "ecb",
            "ezb",
            "eurostat",
            "hicp",
            "hvpi",
            "leitzins",
            "leitzinsen",
            "geldpolitik",
            "zinssatz",
            "euribor",
            "eonia",
            "estr",
            "eurozone",
            "euro-zone",
            "euro area",
            "euroraum",
            "bip",
            "staatsverschuldung",
            "staatsschulden",
            "arbeitslosenquote",
            "aktie",
            "aktien",
            "aktienkurs",
            "dax",
            "dividende",
        ],
    },
    CategoryTable {
        name: "geo",
        keywords: &[
            "weather",
            "climate",
            "temperature",
            "forecast",
            "forecasts",
            "coordinates",
            "coordinate",
            "latitude",
            "longitude",
            "geocod",
            "hurricane",
            "tropical storm",
            "heat wave",
            "cold snap",
            "open-meteo",
            "place name",
            "zip code",
            "zipcode",
            "postal code",
            "rainfall",
            "snowfall",
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
                        Regex::new(&format!(r"\b{}\b", regex::escape(kw))).unwrap_or_else(|e| {
                            panic!("category keyword pattern {kw:?} must compile: {e}")
                        })
                    })
                    .collect()
            })
            .collect()
    })
}

/// Classifies a query by distinct keyword hits, falling back to `general`.
///
/// Ties keep table order: scholarly, legal, patent, financial, geo.
///
/// # Examples
///
/// ```
/// use gossamer::categories::classify_query;
///
/// assert_eq!(classify_query(Some("quantum patent prior art")), "patent");
/// assert_eq!(classify_query(None), "general");
/// ```
pub fn classify_query(query: Option<&str>) -> String {
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn single_topic_routes() {
        assert_eq!(classify_query(Some("quantum patent prior art")), "patent");
        assert_eq!(classify_query(Some("BVerfG urteil")), "legal");
        assert_eq!(classify_query(Some("dax aktienkurs")), "financial");
        assert_eq!(classify_query(Some("weather berlin")), "geo");
        assert_eq!(classify_query(Some("arxiv paper doi")), "scholarly");
    }

    #[test]
    fn fallback_and_ties() {
        assert_eq!(classify_query(None), "general");
        assert_eq!(classify_query(Some("")), "general");
        assert_eq!(classify_query(Some("hello world")), "general");
        assert_eq!(classify_query(Some("BVerfG patent case")), "legal");
    }
}
