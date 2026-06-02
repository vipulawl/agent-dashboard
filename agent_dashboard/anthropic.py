"""
Auto-instrumented Anthropic client.

Drop-in replacement for anthropic.Anthropic — every client.messages.create()
call is automatically logged to RunContext (tokens, stop reason, tool names,
duration). No changes to your agent loop needed.

Usage:
    from agent_dashboard import RunContext
    from agent_dashboard.anthropic import Anthropic

    client = Anthropic()   # same args as anthropic.Anthropic()

    with RunContext("my_agent", db_path="./agent_runs.db") as ctx:
        client.set_context(ctx)
        response = client.messages.create(...)   # auto-logged
        # tool calls still need ctx.log_tool_call() — see README
"""

import time


class _InstrumentedMessages:
    def __init__(self, messages_resource, get_ctx):
        self._messages = messages_resource
        self._get_ctx = get_ctx

    def create(self, *args, **kwargs):
        t0 = time.time()
        response = self._messages.create(*args, **kwargs)
        duration_ms = int((time.time() - t0) * 1000)

        ctx = self._get_ctx()
        if ctx is not None:
            text = next(
                (b.text for b in response.content if hasattr(b, "text")), ""
            )
            tool_names = [
                b.name for b in response.content if b.type == "tool_use"
            ]
            ctx.log_iteration(
                tokens_input=response.usage.input_tokens,
                tokens_output=response.usage.output_tokens,
                stop_reason=response.stop_reason,
                assistant_preview=text[:200],
                tool_names=tool_names,
                duration_ms=duration_ms,
            )

        return response

    def stream(self, *args, **kwargs):
        return self._messages.stream(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._messages, name)


class Anthropic:
    """
    Drop-in replacement for anthropic.Anthropic.

    Accepts all the same constructor arguments. Attach a RunContext with
    set_context(ctx) or pass run_context= at construction time.
    """

    def __init__(self, *args, run_context=None, **kwargs):
        try:
            import anthropic as _anthropic
        except ImportError:
            raise ImportError(
                "anthropic package is required: pip install agent-dashboard[anthropic]"
            )
        self._client = _anthropic.Anthropic(*args, **kwargs)
        self._ctx = run_context
        self.messages = _InstrumentedMessages(
            self._client.messages, lambda: self._ctx
        )

    def set_context(self, ctx) -> None:
        """Attach a RunContext so subsequent messages.create() calls are logged."""
        self._ctx = ctx

    def clear_context(self) -> None:
        """Detach the current RunContext (calls after this won't be logged)."""
        self._ctx = None

    def __getattr__(self, name):
        return getattr(self._client, name)
