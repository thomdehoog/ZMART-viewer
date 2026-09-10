# Readable named-view publication

Named-view HTTP endpoints keep serving a complete immutable generation while
the working aggregate is rebuilt. Each completed Top, Slice or projection view
becomes discoverable immediately; opening a set no longer hides a completed Top
until the remaining views finish. Configuration metadata, coverage and on-demand
chunks use the committed generation too.

`readable.json` selects a private generation under the aggregate's `.readable`
directory. Baked chunks are hard-linked because the baker replaces files
atomically. Original position stores can be overwritten externally, so each new
source revision is copied into a private source folder. Unchanged source folders
are shared directly between generations and compatible views. Large file sets
use at most eight copy workers. Where aggregate hard links are unavailable, they
fall back to copying.

Old generations remain alive while an HTTP request or configuration read holds
them. Background cleanup drops their chunk/metadata caches and deletes source
copies once no registered generation references them. These reader leases belong
to one server process; sharing the same output folders between independent HTTP
servers is not supported by this mechanism. An abrupt process termination can
leave orphan private source folders; automatic crash-orphan scavenging remains
follow-up work. Do not delete `.readable` from an active view.

A fresh source revision temporarily needs extra disk space. This change keeps
readers responsive; it does not remove pyramid computation or certify total
acquisition throughput. The immutable guarantee starts with the first successful
publication using this implementation. Legacy views without a readable pointer
retain their previous serving behavior until republished. Direct filesystem
readers of the working aggregate do not get the HTTP generation guarantee.

Validation: `tests/test_readable_publication.py` exercises HTTP pixels, metadata,
coverage, changes in stack depth, early first-view discovery, shared source
identity, cleanup after the final reader, and recovery from bake, snapshot-copy
and publication-ledger failures. Existing named-view, HTTP server, published
acquisition and depth suites also cover the integration and browser rendering.
