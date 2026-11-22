//! HTML table extraction into rectangular string grids.

use scraper::{ElementRef, Html, Selector};

/// One extracted table: `(caption_or_generated_name, headers, rows)`.
///
/// Rows are rectangular: colspan/rowspan cells are expanded and covered cells are filled with the
/// empty string.
pub type ExtractedTableGrid = (String, Vec<String>, Vec<Vec<String>>);

fn parse_span(value: &str) -> usize {
    value.trim().parse::<usize>().unwrap_or(1).max(1)
}

fn collapse_whitespace(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn cap_text(mut text: String, limit: usize) -> String {
    if text.len() > limit {
        // Leave room for the "..." suffix so the capped result still honors the limit.
        let mut end = limit.saturating_sub(3);
        while !text.is_char_boundary(end) {
            end -= 1;
        }
        text.truncate(end);
        if limit >= 3 {
            text.push_str("...");
        }
    }
    text
}

/// Extracts top-level HTML tables as `(name, headers, rows)` grids.
///
/// Nested tables are skipped. Rows may live in `thead`, `tbody`, `tfoot`, or directly under
/// `table`. The first row becomes `headers` when it contains at least one `th`. Names come from
/// `caption` when present, otherwise `table-N` (1-based in document order).
///
/// # Examples
///
/// ```
/// use gossamer::tables::extract_tables;
///
/// let html = "<table><caption>Demo</caption><tr><th>A</th></tr><tr><td>1</td></tr></table>";
/// let tables = extract_tables(html, 20, 500);
/// assert_eq!(tables[0].0, "Demo");
/// assert_eq!(tables[0].1, vec!["A".to_string()]);
/// assert_eq!(tables[0].2, vec![vec!["1".to_string()]]);
/// ```
pub fn extract_tables(html: &str, max_tables: usize, max_rows: usize) -> Vec<ExtractedTableGrid> {
    const MAX_CELL_CHARS: usize = 1000;
    let document = Html::parse_document(html);
    let table_sel = compile_fixed_selector("table");
    let caption_sel = compile_fixed_selector("caption");

    let mut out: Vec<ExtractedTableGrid> = Vec::new();

    for table in document.select(&table_sel) {
        if out.len() >= max_tables {
            break;
        }
        if table
            .ancestors()
            .any(|a| a.value().as_element().is_some_and(|e| e.name() == "table"))
        {
            continue;
        }

        let mut row_els: Vec<ElementRef> = Vec::new();
        for child in table.child_elements() {
            match child.value().name() {
                "tr" => row_els.push(child),
                "thead" | "tbody" | "tfoot" => {
                    for row in child.child_elements().filter(|c| c.value().name() == "tr") {
                        row_els.push(row);
                    }
                }
                _ => {}
            }
        }
        if row_els.is_empty() {
            continue;
        }

        let mut grid: Vec<Vec<Option<String>>> = Vec::new();
        let mut occupied: Vec<Vec<bool>> = Vec::new();
        let mut first_row_has_th = false;

        for (r, row) in row_els.iter().enumerate() {
            while grid.len() <= r {
                grid.push(Vec::new());
                occupied.push(Vec::new());
            }
            let mut c = 0usize;
            for cell in row.child_elements() {
                let tag = cell.value().name();
                if tag != "td" && tag != "th" {
                    continue;
                }
                loop {
                    while occupied[r].len() <= c {
                        occupied[r].push(false);
                    }
                    if !occupied[r][c] {
                        break;
                    }
                    c += 1;
                }
                let colspan = parse_span(cell.attr("colspan").unwrap_or("1"));
                let rowspan = parse_span(cell.attr("rowspan").unwrap_or("1"));
                if r == 0 && tag == "th" {
                    first_row_has_th = true;
                }
                let text = cap_text(
                    collapse_whitespace(&cell.text().collect::<String>()),
                    MAX_CELL_CHARS,
                );
                for dr in 0..rowspan {
                    let rr = r + dr;
                    while grid.len() <= rr {
                        grid.push(Vec::new());
                        occupied.push(Vec::new());
                    }
                    for dc in 0..colspan {
                        let cc = c + dc;
                        while grid[rr].len() <= cc {
                            grid[rr].push(None);
                            occupied[rr].push(false);
                        }
                        grid[rr][cc] = Some(if dr == 0 && dc == 0 {
                            text.clone()
                        } else {
                            String::new()
                        });
                        occupied[rr][cc] = true;
                    }
                }
                c += colspan;
            }
        }

        let mut rows: Vec<Vec<String>> = grid
            .into_iter()
            .map(|row| {
                row.into_iter()
                    .map(|cell| cell.unwrap_or_default())
                    .collect()
            })
            .collect();
        let n_cols = rows.iter().map(|row| row.len()).max().unwrap_or(0);
        if n_cols == 0 {
            continue;
        }
        for row in rows.iter_mut() {
            row.resize(n_cols, String::new());
        }
        if rows.len() > max_rows {
            rows.truncate(max_rows);
        }

        let mut headers: Vec<String> = Vec::new();
        if first_row_has_th {
            headers = rows.remove(0);
        }
        if rows.is_empty() && headers.is_empty() {
            continue;
        }

        let name = table
            .select(&caption_sel)
            .next()
            .map(|cap| collapse_whitespace(&cap.text().collect::<String>()))
            .filter(|t| !t.is_empty())
            .unwrap_or_else(|| format!("table-{}", out.len() + 1));

        out.push((name, headers, rows));
    }
    out
}

fn compile_fixed_selector(pattern: &'static str) -> Selector {
    Selector::parse(pattern)
        .unwrap_or_else(|e| panic!("invalid fixed CSS selector {pattern:?}: {e}"))
}
