//! Domain-adapter build/parse kernels: the pure parts of
//! `OpenMeteoAdapter`, `FrankfurterAdapter`, `YahooFinanceAdapter`,
//! `NvdAdapter`, `ZenodoAdapter`, `CourtListenerAdapter`,
//! `GovInfoAdapter`, `HudocAdapter`, `PatentsViewAdapter`,
//! `OldpAdapter`, `FederalRegisterAdapter`, `BioRxivAdapter`,
//! `ChemRxivAdapter`, `EurostatAdapter`, `CoinGeckoAdapter` and
//! `AlphaVantageAdapter`.
//!
//! Deliberate split: URL/param building, HTTP, keys, rate limiting and
//! retry stay Python (the existing httpx-mock tests keep working
//! unchanged); response *parsing* — where the historical bugs lived —
//! moves here. Records cross the boundary as JSON **minus `raw`**
//! (Python re-attaches `json.dumps` of the source object, exact by
//! construction). Values keep their JSON types except where the
//! original f-strings them (ids, titles), rendered via Python-`str()`
//! spellings.
//!
//! Pinned by `tests/test_rust_parity_adapters.py`.
//!
//! Layout: `common` holds the shared helpers (error spellings, slicing,
//! truthiness); one module per adapter family after that. The `pub use`
//! globs keep every `crate::adapters::<kernel>` path stable, so `lib.rs`
//! and the Python bridge are untouched by the file split.

mod common;
mod finance;
mod legal;
mod misc;
mod patents;
mod scholar;
#[cfg(test)]
mod tests;

pub use finance::*;
pub use legal::*;
pub use misc::*;
pub use patents::*;
pub use scholar::*;

