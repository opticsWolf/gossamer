from gossamer import agent_tools

def test_research_plan():
    assert agent_tools._service_research_plan("  ", 5, None, 10) == {"error": "research: topic is required"}
    assert agent_tools._service_research_plan(" rust ", 99, 3, 10) == {"topic": "rust", "depth": 10, "plan_results": 3}
    assert agent_tools._service_research_plan("rust", "bad", None, 10) == {"topic": "rust", "depth": 5, "plan_results": 10}
