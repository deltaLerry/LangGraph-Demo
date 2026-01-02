from typing import TypedDict, Dict, Any

class StoryState(TypedDict, total=False):
    user_input: str
    planner_result: Dict[str, Any]
    writer_result: str

