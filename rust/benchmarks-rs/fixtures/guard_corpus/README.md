# guard_corpus

A small labelled corpus for measuring the prompt-injection guard on *our*
traffic mix rather than the detector vendor's.

* `benign/` — ordinary pages of the kind this tool actually fetches:
  documentation, release notes, a forum thread, a product page. Several
  deliberately *talk about* instructions, prompts and system messages
  without carrying any, because that is where a detector produces its
  expensive false positives.
* `injected/` — the same kinds of pages with a planted injection: the
  hidden-comment form, the fake-system-message form, the polite-request
  form, the exfiltration form.

Every file is a fixture, not a target: nothing here is executed, and the
injected text exists so the guard can be measured against it.

Run the measurement with:

    python benchmarks.py --corpus
