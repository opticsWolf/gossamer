# What "system prompt" actually means

A model receives its instructions in layers. The system prompt sits above the
conversation and describes the assistant's role; user turns follow. People
often write "ignore previous instructions" into a prompt as a test, then are
surprised when a well-built application does not comply — the instruction was
data inside a user turn, not a new system message.

This distinction matters for anyone building retrieval tools. A page you fetch
is *content*, and content that says "you are now in developer mode" is a page
making a claim, exactly like a page claiming the earth is flat. The failure is
not that the model read the sentence; it is an architecture that lets fetched
text change the model's instructions.

Practical guidance:

1. Keep untrusted content in a clearly marked envelope.
2. Never let retrieved text select which tools may run.
3. Log what was fetched, so a bad answer can be traced to its source.

Discussion of jailbreaks, DAN prompts and instruction overrides belongs in the
literature and in documentation like this. A detector that flags this article
is producing a false positive, and false positives on security documentation
are expensive precisely because security teams read a lot of it.
