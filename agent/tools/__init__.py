from agent.tools.poi import search_poi, TOOL_DEFINITION as POI_TOOL
from agent.tools.routing import walk_distance
from agent.tools.reviews import fetch_reviews, TOOL_DEFINITION as REVIEWS_TOOL

ALL_TOOLS = [POI_TOOL, REVIEWS_TOOL]

TOOL_REGISTRY = {
    "search_poi": search_poi,
    "fetch_reviews": fetch_reviews,
}
