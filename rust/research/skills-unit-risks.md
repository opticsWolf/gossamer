# Research: `gossamer/skills/` unit risks

Primary sources consulted 2026-11-22. This file is a prerequisite for claiming the first unit.

## 1. `importlib.resources.files(__name__)`

Source: <https://docs.python.org/3/library/importlib.resources.html>

Facts:

- `files(anchor)` returns a `Traversable` representing a resource container. Resources may be
  physical files, zip contents, or other loader-provided objects; they are **not guaranteed** to be
  paths on disk.
- Components passed to `joinpath` use forward slashes (`/`), even on Windows.
- `Traversable.read_text(encoding="utf-8")` is the API the Python unit uses for `SKILL.md`.
- `as_file()` is needed only when a real filesystem path is required. The unit does not need it.
- The module follows the same security model as `open()`: untrusted names must not be passed.

Traps for the port:

1. The Rust crate must not assume `SKILL.md` is a loose file next to the built wheel. For the
   Python-facing PyO3 wrapper, use `include_str!` for the bundled text and keep path resolution in
   the wrapper only if a real path is requested.
2. `_root() / name / "SKILL.md"` must use `/` components, never `Path::join` with Windows
   backslashes derived from user input.
3. Only `SKILL_NAMES = ("gossamer",)` is valid; preserve the exact unknown-name error text.

## 2. Regex offsets and frontmatter parsing

Source: <https://docs.rs/regex/latest/regex/struct.Match.html>

Facts:

- `regex::Match` carries **byte** offsets into the UTF-8 haystack, half-open `[start, end)`.
- Offsets fall on valid UTF-8 codepoint boundaries; slicing is safe but indices are not Python
  character indices.

Traps for the port:

1. Python `_FRONTMATTER` and `_FIELD` operate on `str`; Rust offsets are bytes. Do not expose those
   offsets through the PyO3 wrapper.
2. `_fields()` skips blank lines and lines starting with whitespace, matches `^[A-Za-z_][\w-]*:`,
   and strips one matching pair of `'` or `"`. Reproduce that rule exactly.
3. `\w` in Python is Unicode-aware; spell out ASCII classes for the field-name rule because the
   leading character class is already ASCII.

## 3. Exceptions

Sources:

- <https://pyo3.rs/main/function/error-handling>
- <https://pyo3.rs/main/exception>

Facts:

- Built-in Python exception types are exposed in `pyo3::exceptions` with `new_err` constructors.
- `create_exception!` defines a custom exception class; `import_exception!` imports an existing one.
- `PyResult<T>` maps Rust `Err` into a Python exception; a Rust panic does not match a recorded
  Python error.

Traps for the port:

1. `SkillNotFound` subclasses `KeyError`; it must remain a `KeyError` subclass for Python callers,
   not a generic PyO3 error.
2. `ValueError` messages must match exactly:
   - `unknown skill {name!r}; available: {names}` — Python `!r` quoting, including single quotes.
   - `{name}/SKILL.md has no frontmatter description`
   - `{name}/SKILL.md frontmatter name does not match its directory`
3. `install()` never raises for an existing skill unless `force=True`; it returns status strings
   `"installed"`, `"updated"`, or `"skipped"`.

## 4. Generators / iterators

Source: <https://docs.rs/pyo3/latest/pyo3/types/struct.PyIterator.html>

Facts:

- `PyIterator` wraps a Python iterator; `obj.try_iter()` is the concise equivalent of `iter(obj)`.
- A Rust function returning a sequence can be converted into a Python iterable by returning a
  `Vec`/tuple or by exposing an iterator object through PyO3.

Traps for the port:

- `iter_skills()` is a generator in Python; callers may use `list(iter_skills())` or consume it once.
  Returning a fresh iterator on every call preserves that contract.
- `_copy_tree()` recursively iterates `Path.iterdir()`; order is filesystem-dependent. Recorded
  cases compare the resulting files, not iteration order. Normalize order in the case if needed.

## 5. Data shapes

- `InstallResult` is a frozen dataclass with public attributes `path` and `status`. The PyO3
  wrapper must expose the same attributes; `repr` equality is not part of the recorded contract
  unless a case names it.
- `SKILL_NAMES`, `InstallResult`, `SkillNotFound`, `default_destinations`, `install`, `iter_skills`,
  `skill_description`, `skill_names`, and `skill_text` are all exported. `gate` requires every
  export to be present after the bridge.
