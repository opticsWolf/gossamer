from gossamer.research_providers import _local_name
def test_local_name():
    assert _local_name("{urn:x}Obs") == "Obs"
    assert _local_name("Obs") == "Obs"
    assert _local_name("a}b") == "b"
