from gossamer.meta_extractor import merge_into_document_metadata

def test_merge():
    html = {"meta":{"title":"H","description":"D"},"opengraph":{"title":"O","images":["a"]},"jsonld":[{"x":1}]}
    out = merge_into_document_metadata(html, {"title":"Base","source":"s"})
    assert out["title"] == "Base" and out["description"] == "D"
    assert out["og_title"] == "O" and out["og_images"] == ["a"] and out["jsonld"] == [{"x":1}]
