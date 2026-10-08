"""Apify Actor entry point: one rulemaking identifier/query in, one lifecycle record out."""

from __future__ import annotations

from apify import Actor, Event
from pydantic import ValidationError

from .models.input import ActorInput
from .resolver import Outcome, run_resolution

STATE_KEY = "RESOLUTION_STATE"


def _status_message(outcome: Outcome) -> str:
    r = outcome.result
    stage = r.lifecycle.current_stage if r.lifecycle else "-"
    return f"{r.status} ({r.resolution.confidence or 'n/a'}): stage {stage}, {len(r.related_documents)} documents"


def _input_error(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}" for e in exc.errors())


async def _on_aborting(_event_data: object) -> None:
    Actor.log.info("Run is aborting; exiting without charging.")
    await Actor.exit()


async def main() -> None:
    async with Actor:
        Actor.on(Event.ABORTING, _on_aborting)
        store = await Actor.open_key_value_store()
        state = await store.get_value(STATE_KEY) or {}
        if state.get("completed"):
            Actor.log.info("Resolution already completed in this run; not repeating it or charging again.")
            return

        try:
            actor_input = ActorInput.model_validate(await Actor.get_input() or {})
        except ValidationError as exc:
            await Actor.fail(status_message=f"Invalid input: {_input_error(exc)}")
            return

        outcome = await run_resolution(actor_input)
        result = outcome.result
        await Actor.push_data(result.to_record())
        await store.set_value(STATE_KEY, {"completed": True, "charged": False})
        Actor.log.info("Resolution %s in %.0f ms", result.status, outcome.elapsed_ms)

        if result.billing.billable and result.billing.event_name:
            charge = await Actor.charge(event_name=result.billing.event_name)
            await store.set_value(STATE_KEY, {"completed": True, "charged": True})
            if charge.event_charge_limit_reached:
                Actor.log.info("Charge limit reached; result was delivered before the limit applied.")
        await Actor.set_status_message(_status_message(outcome))
