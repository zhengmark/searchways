"""多智能体协同系统 —— Plan-Execute-Review-Refine 架构

使用方式:
    from agent.multi_agent.orchestrator import run_multi_agent, AgentSession
    session = AgentSession(); session.default_city = '北京'
    result, session = run_multi_agent('今天下午带我妈逛逛朝阳区', session)

架构:
    Orchestrator → Intent Agent → POI Strategy Agent → Route Engine
              → Narrator Agent → Reviewer Agent (↻ up to 2 loops)
              → Mermaid + Leaflet HTML output
"""
from agent.multi_agent.orchestrator import run_multi_agent
