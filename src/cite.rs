//! PyO3 binding for citation kernels.
//!
//! All record-building and formatter logic lives in the pure `gossamer` core (ADR-012). This
//! module contains only the Python-visible record class, field marshaling, and exception mapping.

use pyo3::exceptions::{
    PyAttributeError, PyIndexError, PyKeyError, PyTypeError, PyValueError,
};
use pyo3::prelude::*;
use pyo3::types::PyDict;

pub use gossamer::cite::py_value_repr;
use gossamer::cite::{
    CitationError, CitationErrorKind, Record, abstract_from_raw, apa_approx,
    citation_record_from_json as core_citation_record_from_json, mla_approx, to_bibtex, to_csl_json,
    venue_from_raw,
};

#[pyclass]
pub struct BibliographicRecord {
    #[pyo3(get, set)]
    pub title: Option<String>,
    #[pyo3(get, set)]
    pub authors: Vec<String>,
    #[pyo3(get, set)]
    pub year: Option<String>,
    #[pyo3(get, set)]
    pub month: Option<String>,
    #[pyo3(get, set)]
    pub day: Option<String>,
    #[pyo3(get, set)]
    pub doi: Option<String>,
    #[pyo3(get, set)]
    pub url: Option<String>,
    #[pyo3(get, set)]
    pub venue: Option<String>,
    #[pyo3(get, set)]
    pub publisher: Option<String>,
    #[pyo3(get, set, name = "abstract")]
    pub abstract_text: Option<String>,
    #[pyo3(get, set)]
    pub extra: Py<PyAny>,
    #[pyo3(get, set)]
    pub id: Option<String>,
    #[pyo3(get, set)]
    pub kind: Option<String>,
}

impl Clone for BibliographicRecord {
    fn clone(&self) -> Self {
        Python::attach(|py| Self {
            title: self.title.clone(),
            authors: self.authors.clone(),
            year: self.year.clone(),
            month: self.month.clone(),
            day: self.day.clone(),
            doi: self.doi.clone(),
            url: self.url.clone(),
            venue: self.venue.clone(),
            publisher: self.publisher.clone(),
            abstract_text: self.abstract_text.clone(),
            extra: self.extra.clone_ref(py),
            id: self.id.clone(),
            kind: self.kind.clone(),
        })
    }
}

impl BibliographicRecord {
    fn as_record(&self) -> Record {
        Record {
            title: self.title.clone(),
            authors: self.authors.clone(),
            year: self.year.clone(),
            month: self.month.clone(),
            day: self.day.clone(),
            doi: self.doi.clone(),
            url: self.url.clone(),
            venue: self.venue.clone(),
            publisher: self.publisher.clone(),
            abstract_text: self.abstract_text.clone(),
            id: self.id.clone(),
            kind: self.kind.clone(),
        }
    }
}

#[pymethods]
impl BibliographicRecord {
    #[new]
    #[pyo3(signature = (title=None, authors=None, year=None, month=None, day=None, doi=None, url=None, venue=None, publisher=None, r#abstract=None, extra=None, id=None, kind=None))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        py: Python,
        title: Option<String>,
        authors: Option<Vec<String>>,
        year: Option<String>,
        month: Option<String>,
        day: Option<String>,
        doi: Option<String>,
        url: Option<String>,
        venue: Option<String>,
        publisher: Option<String>,
        r#abstract: Option<String>,
        extra: Option<Py<PyAny>>,
        id: Option<String>,
        kind: Option<String>,
    ) -> Self {
        let empty = PyDict::new(py).into_any().unbind();
        BibliographicRecord {
            title,
            authors: authors.unwrap_or_default(),
            year,
            month,
            day,
            doi,
            url,
            venue,
            publisher,
            abstract_text: r#abstract,
            extra: extra.unwrap_or(empty),
            id,
            kind,
        }
    }
}

fn raise_mapped(err: &CitationError) -> PyErr {
    let detail = err.detail().to_owned();
    match err.kind() {
        CitationErrorKind::ValueError => PyValueError::new_err(detail),
        CitationErrorKind::TypeError => PyTypeError::new_err(detail),
        CitationErrorKind::AttributeError => PyAttributeError::new_err(detail),
        CitationErrorKind::IndexError => PyIndexError::new_err(detail),
        CitationErrorKind::KeyError => match detail.parse::<i64>() {
            Ok(n) => PyKeyError::new_err(n),
            Err(_) => PyKeyError::new_err(detail),
        },
    }
}

fn borrow_records(records: Vec<Bound<'_, BibliographicRecord>>) -> Vec<Record> {
    records.iter().map(|r| r.borrow().as_record()).collect()
}

#[pyfunction]
pub fn citation_record_from_json(py: Python, result_json: &str) -> PyResult<BibliographicRecord> {
    let rec = core_citation_record_from_json(result_json).map_err(|e| raise_mapped(&e))?;
    let empty = PyDict::new(py).into_any().unbind();
    Ok(BibliographicRecord {
        title: rec.title,
        authors: rec.authors,
        year: rec.year,
        month: rec.month,
        day: rec.day,
        doi: rec.doi,
        url: rec.url,
        venue: rec.venue,
        publisher: rec.publisher,
        abstract_text: rec.abstract_text,
        extra: empty,
        id: rec.id,
        kind: rec.kind,
    })
}

#[pyfunction]
pub fn cite_bibtex(records: Vec<Bound<'_, BibliographicRecord>>) -> String {
    to_bibtex(&borrow_records(records))
}

#[pyfunction]
pub fn cite_csl_json(records: Vec<Bound<'_, BibliographicRecord>>) -> PyResult<String> {
    to_csl_json(&borrow_records(records)).map_err(|e| raise_mapped(&e))
}

#[pyfunction]
pub fn cite_apa_approx(records: Vec<Bound<'_, BibliographicRecord>>) -> String {
    apa_approx(&borrow_records(records))
}

#[pyfunction]
pub fn cite_mla_approx(records: Vec<Bound<'_, BibliographicRecord>>) -> String {
    mla_approx(&borrow_records(records))
}

#[pyfunction]
pub fn cite_venue_from_raw(raw: Option<&str>) -> PyResult<Option<String>> {
    venue_from_raw(raw).map_err(|e| raise_mapped(&e))
}

#[pyfunction]
pub fn cite_abstract_from_raw(raw: Option<&str>) -> PyResult<Option<String>> {
    abstract_from_raw(raw).map_err(|e| raise_mapped(&e))
}
