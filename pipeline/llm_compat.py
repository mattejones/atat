"""
llm_compat.py — small shims so the pipeline works across Anthropic SDK releases.
"""

import inspect


def with_temperature(method, value) -> dict:
    """
    `{"temperature": value}` if this SDK method still takes a `temperature` argument, else `{}`.

    Newer anthropic releases (2.x) dropped `temperature` from `messages.create()` and
    `messages.stream()`. Passing it there raises a TypeError in Python before any request is
    made — which the "model rejected temperature" fallbacks (they catch BadRequestError from the
    API) never see. Without it the model's default sampling is used, which is exactly what those
    fallbacks already do for models that refuse the parameter.

        client.messages.create(**with_temperature(client.messages.create, 0.3), **kwargs)
    """
    try:
        params = inspect.signature(method).parameters
    except (TypeError, ValueError):
        return {"temperature": value}   # can't tell — keep the old behaviour
    takes_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    return {"temperature": value} if "temperature" in params or takes_kwargs else {}
