# hello_world/__main__.py
from __future__ import annotations

import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from .agent_executor import HelloWorldAgentExecutor  # type: ignore[import-untyped]


def main(host: str = "0.0.0.0", port: int = 9999) -> None:
    # Basis-Skill
    skill = AgentSkill(
        id="hello_world",
        name="Returns hello world",
        description="just returns hello world",
        tags=["hello world"],
        examples=["hi", "hello world"],
    )

    # Erweiterter Skill (nur in extended card)
    extended_skill = AgentSkill(
        id="super_hello_world",
        name="Returns a SUPER Hello World",
        description="A more enthusiastic greeting, only for authenticated users.",
        tags=["hello world", "super", "extended"],
        examples=["super hi", "give me a super hello"],
    )

    jsonrpc_url = f"http://{host}:{port}/a2a/v1/jsonrpc"

    # Öffentliche AgentCard (Spec-konform)
    public_agent_card = AgentCard(
        name="Hello World Agent",
        description="Just a hello world agent",
        url=jsonrpc_url,                            # <- JSON-RPC URL
        preferred_transport="JSONRPC",              # <- explizit setzen
        version="1.0.0",
        default_input_modes=["text/plain"],         # <- MIME types
        default_output_modes=["text/plain", "application/json"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[skill],
        supports_authenticated_extended_card=True,
        additional_interfaces=[                     # <- Best-Practice-Eintrag
            {"url": jsonrpc_url, "transport": "JSONRPC"}
        ],
    )

    # Authentifizierte erweiterte AgentCard
    specific_extended_agent_card = public_agent_card.model_copy(
        update={
            "name": "Hello World Agent - Extended Edition",
            "description": "The full-featured hello world agent for authenticated users.",
            "version": "1.0.1",
            "skills": [skill, extended_skill],
        }
    )

    request_handler = DefaultRequestHandler(
        agent_executor=HelloWorldAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=public_agent_card,
        http_handler=request_handler,
        extended_agent_card=specific_extended_agent_card,
    )

    uvicorn.run(server.build(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
