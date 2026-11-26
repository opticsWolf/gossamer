from gossamer.search import _service_failover_results, _service_merged_results


FAILOVER_CASES = [
    ([], 5),
    ([["duckduckgo", None]], 5),
    ([["duckduckgo", []], ["google", [{"url": "https://example.com/a"}]]], 5),
    (
        [
            ["duckduckgo", None],
            [
                "google",
                [
                    {"url": "https://example.com/a"},
                    {"url": "https://WWW.Example.com/a"},
                    {"title": "no url"},
                ],
            ],
        ],
        5,
    ),
    ([["google", [{"url": "not a url"}, {"url": "not a url"}]]], 5),
]

MERGED_CASES = [
    ([], 5),
    ([["duckduckgo", None]], 5),
    (
        [
            [
                "google",
                [
                    {"url": "https://example.com/a"},
                    {"url": "https://example.com/b"},
                ],
            ],
            [
                "bing",
                [
                    {"url": "https://example.com/a"},
                    {"url": "https://example.com/c"},
                ],
            ],
        ],
        3,
    ),
    (
        [
            ["google", [1, {"url": "https://example.com/a"}, {"no": "url"}]],
            ["bing", [{"url": "https://example.com/b"}]],
        ],
        5,
    ),
    (
        [
            ["google", [{"url": "https://example.com/a"}]],
            ["bing", [{"url": "https://example.com/a"}]],
        ],
        1,
    ),
]


def test_failover_results_cases():
    expected = [
        {"results": [], "any_ok": False, "dropped_dupes": 0},
        {"results": [], "any_ok": False, "dropped_dupes": 0},
        {"results": [], "any_ok": True, "dropped_dupes": 0},
        {
            "results": [
                {"url": "https://example.com/a"},
                {"title": "no url"},
            ],
            "any_ok": True,
            "dropped_dupes": 1,
        },
        {
            "results": [{"url": "not a url"}, {"url": "not a url"}],
            "any_ok": True,
            "dropped_dupes": 0,
        },
    ]
    for case, want in zip(FAILOVER_CASES, expected):
        assert _service_failover_results(*case) == want


def test_merged_results_cases():
    expected = [
        {"results": [], "any_ok": False, "dropped_dupes": 0},
        {"results": [], "any_ok": False, "dropped_dupes": 0},
        {
            "results": [
                {"url": "https://example.com/a"},
                {"url": "https://example.com/b"},
                {"url": "https://example.com/c"},
            ],
            "any_ok": True,
            "dropped_dupes": 1,
        },
        {
            "results": [
                {"url": "https://example.com/a"},
                {"no": "url"},
                {"url": "https://example.com/b"},
            ],
            "any_ok": True,
            "dropped_dupes": 0,
        },
        {
            "results": [{"url": "https://example.com/a"}],
            "any_ok": True,
            "dropped_dupes": 0,
        },
    ]
    for case, want in zip(MERGED_CASES, expected):
        assert _service_merged_results(*case) == want
