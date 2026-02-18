"""
Usage:

    python agent.py
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

from google.adk.agents import Agent
from google.adk.runners import InMemoryRunner
from google.genai import types

from tools.form_tools import (
    parse_form,
    autofill_form,
    validate_form,
    explain_field,
)


def create_agent() -> Agent:

    model_id = os.environ.get("GEMINI_MODEL", "gemini-pro")
    return Agent(
        name="digital_paperwork_butler",
        model=model_id,
        description=(
            "A concierge agent that automates form filling.  It can parse PDF "
            "forms, autofill known details, validate missing or invalid fields, "
            "and explain confusing form terminology."
        ),
        instruction=(
            "You are the Digital Paperwork Butler.  You politely assist the user "
            "with administrative forms by calling the available tools.  When "
            "presented with a PDF path, extract the fields and identify what "
            "information you have and what you still need.  Use the autofill "
            "tool to populate blanks from the user's profile, validate the "
            "results, and explain any jargon.  Always act as a courteous "
            "concierge."
        ),
        tools=[parse_form, autofill_form, validate_form, explain_field],
    )


def run_console(agent: Agent, app_name: str = "paperwork_app", user_id: str = "user") -> None:
    """Run an interactive console to chat with the agent.

    Parameters
    ----------
    agent: Agent
        The root agent to run.
    app_name: str, default "paperwork_app"
        Identifier for the app.  Used by the session service.
    user_id: str, default "user"
        Identifier for the user.  Sessions are scoped to a user.
    """
    runner = InMemoryRunner(agent=agent, app_name=app_name)
    # Create a session asynchronously
    session = asyncio.run(runner.session_service.create_session(app_name=app_name, user_id=user_id))

    print("Digital Paperwork Butler ready. Type 'quit' to exit.\n")
    while True:
        try:
            user_message = input("You > ").strip()
        except EOFError:
            break
        if user_message.lower() in {"quit", "exit"}:
            break
        if not user_message:
            continue
        content = types.Content(role="user", parts=[types.Part.from_text(text=user_message)])
        print("Agent > ", end="", flush=True)
        # Run synchronously; iterate through events
        for event in runner.run(user_id=user_id, session_id=session.id, new_message=content):
            # Print only textual parts of the response
            if event.content and event.content.parts:
                part = event.content.parts[0]
                if hasattr(part, "text") and part.text:
                    print(part.text, end="", flush=True)
        print("\n", flush=True)


if __name__ == "__main__":
    agent = create_agent()
    run_console(agent)
