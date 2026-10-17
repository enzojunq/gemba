"""Message builder. The order is fixed so Crusoe's KV/prefix cache stays warm (spec 10.5)."""
from __future__ import annotations


def build_messages(role_prompt: str, playbook_rules: list[str], board_snapshot: str, task: str) -> list[dict]:
    """Build the messages for every org LLM call.

    Order, never reorder (most static first, most volatile last):
      1. system: the role prompt (static per role)
      2. user:   ``## Playbook rules for your role`` (changes per playbook version, "none" if empty)
                 ``## Board`` (changes per day)
                 ``## Task`` (changes per call)
    """
    rules = "\n".join(f"- {r}" for r in playbook_rules) if playbook_rules else "none"
    user = (f"## Playbook rules for your role\n{rules}\n\n"
            f"## Board\n{board_snapshot}\n\n"
            f"## Task\n{task}")
    return [{"role": "system", "content": role_prompt}, {"role": "user", "content": user}]
