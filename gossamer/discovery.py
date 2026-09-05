"""Site resource discovery (Tier 3.12) for the toolbox.

Extracted from ``agent_tools.py`` during the composition split. Fetches a
page once, finds ``<link rel=alternate>`` feed declarations, and probes the
site root for ``/sitemap.xml`` with bounded hops. All shared toolbox state is
read through ``self._tb`` (the toolbox).
"""

import json
import logging
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)



class ResourceDiscovery:
    """Site resource discovery (Tier 3.12).

    Thin orchestrator moved out of the WebResearcherToolbox facade
    (composition phase 6). Fetches a page once, finds <link rel="alternate">
    feed declarations, and probes the site root for /sitemap.xml with
    bounded hops. All shared toolbox state is read through ``self._tb``
    (the toolbox).
    """

    def __init__(self, tb):
        self._tb = tb

    _DISCOVER_MAX_SITEMAP_FETCHES = 10
    _DISCOVER_MAX_INDEX_HOPS = 3
    _DISCOVER_MAX_URLS_PER_SITEMAP = 500
    _DISCOVER_MAX_URLS = 1000
    _FEED_TYPE_PREFIXES = (
        "application/rss+xml",
        "application/atom+xml",
        "application/feed+json",
    )
    _FEED_LINK_RE = re.compile(
        r"<link\b[^>]*rel\s*=\s*[\"']?alternate[\"']?[^>]*>",
        re.IGNORECASE | re.DOTALL,
    )
    _LINK_ATTR_RE = re.compile(
        r"(?P<attr>type|href)\s*=\s*[\"'](?P<value>[^\"']*)[\"']",
        re.IGNORECASE,
    )

    def discover_resources(self, url: str) -> str:
        """Discover a site's structured resources (Tier 3.12).

        A cheaper alternative to link-graph crawling: fetch the page once
        and look for ``<link rel="alternate">`` feed declarations, then
        probe the site root for ``/sitemap.xml`` (following sitemap
        indexes with bounded hops and fetch counts). Returns deduplicated,
        budgeted lists of feed URLs and sitemap page URLs.

        All probes are best-effort: a missing sitemap, a malformed feed,
        or a failed page fetch degrades the result instead of raising.

        Parameters
        ----------
        url : str
            A page or site URL to discover resources for.

        Returns
        -------
        str
            JSON with ``url``, ``site_root``, ``feeds`` (list of
            ``{url, type}``), ``sitemaps`` (list of ``{url, kind, count}``
            with kind ``urlset``/``index``), the merged deduplicated
            ``urls`` list, ``count``, and ``truncated`` (true when a
            budget cap cut the list short).
        """
        url, url_error = self._tb._prepare_url(url)
        if url_error is not None:
            return json.dumps(url_error, indent=2)

        if self._tb._robots_disallows(url):
            logger.warning("URL disallowed by robots.txt: %s", url)
            return json.dumps(
                {"warning": "URL disallowed by robots.txt", "url": url}, indent=2
            )
        if not self._tb._claim_in_flight(url):
            logger.warning("URL already visited or in flight: %s", url)
            return json.dumps(
                {"warning": "URL already visited", "url": url}, indent=2
            )
        self._tb._rate_limit_domain(url)

        try:
            feeds: list = []
            sitemaps: list = []
            found: dict = {}  # ordered dedupe of discovered page URLs
            truncated = False

            # 1) Page fetch: feed alternates from the raw HTML (static
            #    path only; browser renders expose no raw DOM).
            _md, _links, _meta, _method, page_html = (
                self._tb._fetch._fetch_html_with_html(url)
            )
            if page_html:
                feeds = self._find_feed_links(page_html, url)

            # 2) Sitemap probe at the site root (same origin as the
            #    already-validated input URL).
            parsed = urlparse(url)
            if parsed.scheme and parsed.netloc:
                site_root = f"{parsed.scheme}://{parsed.netloc}"
            else:
                site_root = None
            sitemaps, found, truncated = self._probe_sitemaps(site_root)

            return json.dumps(
                {
                    "url": url,
                    "site_root": site_root,
                    "feeds": feeds,
                    "sitemaps": sitemaps,
                    "urls": list(found.keys()),
                    "count": len(found),
                    "truncated": truncated,
                },
                indent=2,
            )
        except Exception as e:
            logger.error("Discovery failed for %s: %s", url, e)
            return json.dumps(
                {"error": f"Discovery failed: {str(e)}", "url": url}, indent=2
            )
        finally:
            # S5: release exactly once on every exit path. Discovery does
            # not mark the page visited -- it is metadata-level, so the
            # page stays inspectable afterwards.
            self._tb._release_in_flight(url)

    def _find_feed_links(self, html: str, base_url: str) -> list:
        """Tier 3.12: find ``<link rel="alternate">`` feed declarations.

        Only feed content-types (RSS/Atom/Feed-JSON) count; language
        alternates (``hreflang``) are ignored. Relative hrefs are
        absolutized against *base_url*. Regex-based on purpose: the page
        is arbitrary HTML, not well-formed XML.
        """
        feeds = []
        for tag in self._FEED_LINK_RE.findall(html):
            attrs = {
                m.group("attr").lower(): m.group("value")
                for m in self._LINK_ATTR_RE.finditer(tag)
            }
            link_type = attrs.get("type", "").lower().split(";")[0].strip()
            if not any(
                link_type.startswith(prefix)
                for prefix in self._FEED_TYPE_PREFIXES
            ):
                continue
            href = (attrs.get("href") or "").strip()
            if not href:
                continue
            feeds.append({"url": urljoin(base_url, href), "type": link_type})
        return feeds

    def _probe_sitemaps(self, site_root: Optional[str]):
        """Tier 3.12: probe ``/sitemap.xml`` and follow indexes (bounded).

        Returns ``(sitemaps, found, truncated)`` where *sitemaps* is a
        list of ``{url, kind, count}`` records in fetch order, *found* is
        an ordered dict of discovered page URLs, and *truncated* is true
        when the total-URL cap cut the list short. Fetch/parse failures
        are logged and skipped (best effort).
        """
        sitemaps: list = []
        found: dict = {}
        truncated = False
        if not site_root:
            return sitemaps, found, truncated

        first = site_root.rstrip("/") + "/sitemap.xml"
        try:
            self._tb._validate_url(first)
        except Exception:
            return sitemaps, found, truncated

        import xml.etree.ElementTree as ET

        queue = [first]
        hops = {first: 0}
        seen = set()
        fetched = 0
        while queue and fetched < self._DISCOVER_MAX_SITEMAP_FETCHES:
            sm_url = queue.pop(0)
            if sm_url in seen:
                continue
            seen.add(sm_url)
            # Sitemap entries are site-supplied URLs: validate (SSRF) and
            # throttle (politeness) like any other fetch (review B.7).
            try:
                self._tb._validate_url(sm_url)
            except Exception:
                logger.info("Sitemap URL rejected by policy: %s", sm_url)
                continue
            self._tb._rate_limit_domain(sm_url)
            fetched += 1

            try:
                _md, _links, _meta, _method, xml_text = self._tb._fetch._static_fetch(
                    sm_url, keep_html=True
                )
            except Exception as e:
                logger.info("Sitemap probe failed for %s: %s", sm_url, e)
                continue
            if not xml_text:
                continue
            try:
                # lstrip: whitespace before the <?xml?> declaration is
                # legal XML but expat rejects it; some hosts emit it.
                root = ET.fromstring(xml_text.lstrip())
            except ET.ParseError as e:
                logger.info("Sitemap parse failed for %s: %s", sm_url, e)
                continue

            kind_tag = root.tag.rsplit("}", 1)[-1]  # namespace-safe
            locs = [
                (el.text or "").strip()
                for el in root.iter()
                if el.tag.rsplit("}", 1)[-1] == "loc" and (el.text or "").strip()
            ]
            if kind_tag not in ("urlset", "sitemapindex") or not locs:
                continue
            if len(locs) > self._DISCOVER_MAX_URLS_PER_SITEMAP:
                locs = locs[: self._DISCOVER_MAX_URLS_PER_SITEMAP]
                truncated = True
            kind = "index" if kind_tag == "sitemapindex" else "urlset"
            locs = [urljoin(sm_url, loc) for loc in locs]
            sitemaps.append({"url": sm_url, "kind": kind, "count": len(locs)})

            hop = hops.get(sm_url, 0)
            if kind == "index" and hop + 1 <= self._DISCOVER_MAX_INDEX_HOPS:
                for child in locs:
                    if child not in seen and child not in queue:
                        hops[child] = hop + 1
                        queue.append(child)
            else:
                for page_url in locs:
                    if page_url not in found:
                        if len(found) >= self._DISCOVER_MAX_URLS:
                            truncated = True
                            break
                        found[page_url] = None
        return sitemaps, found, truncated
