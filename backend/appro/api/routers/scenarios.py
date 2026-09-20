"""Saved what-if scenarios and comparison with the baseline."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...data.store import Scenario, ScenarioEventRow, audit
from ...services import mrp_service
from ...services.context import AppContext
from .. import presenters as P
from .. import schemas as S
from ..deps import ctx_dep, current_user, session_dep

router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])


def _out(sc: Scenario) -> S.ScenarioOut:
    return S.ScenarioOut(id=sc.id, name=sc.name, description=sc.description, status=sc.status, params=sc.params,
                         events=[S.ScenarioEventOut(id=e.id, seq=e.seq, kind=e.kind, payload=e.payload, label=e.label)
                                 for e in sc.events],
                         created_by=sc.created_by, created_at=sc.created_at, updated_at=sc.updated_at)


def _check_events(events: list[S.ScenarioEventIn]) -> None:
    bad = [e.kind for e in events if e.kind not in S.EVENT_KINDS]
    if bad:
        raise HTTPException(422, f"Types d'événement inconnus : {bad}. Attendus : {list(S.EVENT_KINDS)}")


@router.get("", response_model=list[S.ScenarioOut])
def list_scenarios(session: Session = Depends(session_dep)):
    return [_out(s) for s in session.scalars(select(Scenario).order_by(Scenario.updated_at.desc())).all()]


@router.post("", response_model=S.ScenarioOut, status_code=201)
def create_scenario(body: S.ScenarioIn, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                    user: str = Depends(current_user)):
    _check_events(body.events)
    sc = Scenario(name=body.name, description=body.description, params_json=json.dumps(body.params, default=str),
                  created_by=user)
    for i, e in enumerate(body.events):
        sc.events.append(ScenarioEventRow(seq=i, kind=e.kind, payload_json=json.dumps(e.payload, default=str), label=e.label))
    session.add(sc)
    audit(session, user, "create", "scenario", sc.id, None, {"name": body.name, "events": len(body.events)})
    session.commit()
    ctx.bump()
    return _out(sc)


@router.get("/{scenario_id}", response_model=S.ScenarioOut)
def get_scenario(scenario_id: str, session: Session = Depends(session_dep)):
    sc = session.get(Scenario, scenario_id)
    if sc is None:
        raise HTTPException(404, "Scénario inconnu")
    return _out(sc)


@router.put("/{scenario_id}", response_model=S.ScenarioOut)
def update_scenario(scenario_id: str, body: S.ScenarioIn, ctx: AppContext = Depends(ctx_dep),
                    session: Session = Depends(session_dep), user: str = Depends(current_user)):
    sc = session.get(Scenario, scenario_id)
    if sc is None:
        raise HTTPException(404, "Scénario inconnu")
    _check_events(body.events)
    sc.name, sc.description = body.name, body.description
    sc.params_json = json.dumps(body.params, default=str)
    sc.events.clear()
    for i, e in enumerate(body.events):
        sc.events.append(ScenarioEventRow(seq=i, kind=e.kind, payload_json=json.dumps(e.payload, default=str), label=e.label))
    audit(session, user, "update", "scenario", sc.id, None, {"name": body.name, "events": len(body.events)})
    session.commit()
    ctx.bump()
    return _out(sc)


@router.delete("/{scenario_id}", status_code=204)
def delete_scenario(scenario_id: str, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                    user: str = Depends(current_user)):
    sc = session.get(Scenario, scenario_id)
    if sc is None:
        raise HTTPException(404, "Scénario inconnu")
    audit(session, user, "delete", "scenario", sc.id, None, {"name": sc.name})
    session.delete(sc)
    session.commit()
    ctx.bump()


@router.get("/{scenario_id}/compare", response_model=S.CompareResponse)
def compare(scenario_id: str, planner: str | None = None, ctx: AppContext = Depends(ctx_dep),
            session: Session = Depends(session_dep)):
    if session.get(Scenario, scenario_id) is None:
        raise HTTPException(404, "Scénario inconnu")
    base = mrp_service.compute(ctx, session, planner=planner)
    scen = mrp_service.compute(ctx, session, planner=planner, scenario_id=scenario_id)
    return S.CompareResponse(as_of=base.as_of, base_kpis=P.cockpit_kpis(base), scenario_kpis=P.cockpit_kpis(scen),
                             articles=P.compare_articles(base, scen), diagnostics=scen.diagnostics)
