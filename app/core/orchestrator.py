"""Orchestrator — 统一 Agent 路线规划入口，支持多轮对话 + 用户画像持久化.

流程:
  LLM(工具调用) → geocode → query_clusters → build_route → 解说输出
  每轮结束: 保存 session 到 users/{user_id}.json
"""

from app.shared.utils import AgentSession


def run_multi_agent(
    user_input: str, session: AgentSession = None, user_id: str = "default", progress_callback=None
) -> tuple:
    """统一路线规划入口，支持多轮对话.

    Args:
        user_input: 用户当前输入
        session: AgentSession（内部使用，可传 None）
        user_id: 用户 ID（默认 "default"）
        progress_callback: SSE 进度回调 emoji,msg

    Returns:
        (回复文本, AgentSession)
    """
    if session is None:
        session = AgentSession()

    from app.core.route_agent import run_unified_agent

    return run_unified_agent(user_input, session, user_id, progress_callback=progress_callback)
