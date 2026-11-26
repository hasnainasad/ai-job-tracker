"""Phase 7d: tracing = a recording of every agent run, step by step.

An agent makes its own choices, so when it does something odd you need to see what it
saw: each model call, each tool call with its arguments and answer, timings, tokens.
A trace is that recording; you open it in a web UI.

Where traces go (first match wins):
  AGENT_TRACING=off                        nowhere
  LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY Langfuse (cloud.langfuse.com or your own server;
                                           needs `uv sync --extra tracing`)
  OPENAI_API_KEY                           the OpenAI dashboard (platform.openai.com/traces),
                                           the Agents SDK's built-in default
  otherwise                                nowhere

Privacy: traces include the text the agents read and wrote (job postings, your resume)
and go to YOUR Langfuse or OpenAI account. Set AGENT_TRACE_CONTENT=off to record only
the steps, without the text.
"""
import os
import sys


def include_content() -> bool:
    return os.getenv("AGENT_TRACE_CONTENT", "on").lower() not in ("off", "0", "false", "no")


def setup_tracing() -> str:
    """Call once before running agents. Returns where traces go: langfuse | openai | off."""
    from agents import set_tracing_disabled

    if os.getenv("AGENT_TRACING", "on").lower() in ("off", "0", "false", "no"):
        set_tracing_disabled(True)
        return "off"
    if os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"):
        try:
            from langfuse import get_client
            from openinference.instrumentation.openai_agents import (
                OpenAIAgentsInstrumentor,
            )
        except ImportError:
            print("Langfuse keys found but the packages aren't installed: uv sync --extra tracing",
                  file=sys.stderr)
        else:
            get_client()  # reads LANGFUSE_* from the environment and starts the exporter
            OpenAIAgentsInstrumentor().instrument()  # SDK trace events -> OpenTelemetry -> Langfuse
            return "langfuse"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    set_tracing_disabled(True)
    return "off"


def flush_tracing(where: str) -> None:
    """Send any buffered trace data before the program exits."""
    if where == "langfuse":
        from langfuse import get_client

        get_client().flush()
    elif where == "openai":
        from agents.tracing import get_trace_provider

        get_trace_provider().force_flush()
