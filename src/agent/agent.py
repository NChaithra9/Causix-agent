"""Agent definitions for the project."""

from __future__ import annotations
from datetime import timedelta
from typing import Dict,Any

from aetherion_sdk import agent, toolExecutor


@agent()
async def Causix(payload: Dict[str, Any]) -> dict:
    """Agent that:
    - Greets the user
    - Analyzes the input text (with a timeout example)
    - Enriches the greeting
    """

    user_input = payload.get('input')
    greeting = await toolExecutor.execute("greet_user", user_input)

    # Pass start_to_close_timeout when executing a tool that may run longer than the default 30 seconds.
    # This helps prevent buggy tools that never return or get stuck in loops.
    analysis = await toolExecutor.execute(
        "analyze_text",
        user_input,
        start_to_close_timeout=timedelta(minutes=5),
    )

    enriched = await toolExecutor.execute("enrich_greeting", user_input)

    return {
        "user_input": user_input,
        "greeting": greeting,
        "enriched_greeting": enriched,
        "analysis": analysis,
        "status": "success",
    }
