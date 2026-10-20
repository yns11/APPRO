"""Annuler / rétablir : the journal of user gestures with the state of every touched row before and after.

One *action* = one gesture of the user (a cell typed, a fill or a paste of the grid, a reference row, a
weekly parameter, a PDP save…).  An action holds, for every row it touched, the row's state **before**
and **after** ; undoing writes the *before* states back, redoing writes the *after* states.

Rules :

* actions are undone in strict chronological order **per user** : undoing an older action first
  undoes the newer ones of the same user (« annuler jusqu'ici ») ; redoing goes the other way ;
* a new action **discards** the actions the user had undone (no branching) ;
* an action is undone / redone only when every touched row is still in the state the action left it
  (otherwise « modifié depuis par X » and nothing is written) ;
* a user undoes their own actions ; an administrator may undo anyone's ;
* an action is kept :data:`RETENTION_DAYS` days ;
* some gestures are journalised but **not undoable** (a reference table replaced by a file, a PDP
  version deleted) : the screen asks for a confirmation instead.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from fastapi import Request, Response
from sqlalchemy import Date, DateTime, select
from sqlalchemy.orm import Session

from ..data.reference import schema_of
from ..data.store import (
    REF_MODELS,
    AppAction,
    AppAdjustment,
    AppCellFlag,
    AppMeta,
    AppPlanCell,
    ParamOverride,
    PdpVersion,
    ProgramScrap,
    audit,
    utcnow,
)

RETENTION_DAYS = 7
#: a gesture split over several requests (a bulk edit of the reference screen) is merged into one action
#: when its requests arrive within this delay
GROUP_WINDOW = dt.timedelta(minutes=30)
GROUP_HEADER = "x-action-group"
PDP_ACTIVE = "pdp_active"


class ActionError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# =============================================================================
# Entities: how a touched row is identified, read and written back
# =============================================================================
@dataclass(frozen=True)
class Spec:
    model: type
    key: tuple[str, ...]
    fields: tuple[str, ...]


SPECS: dict[str, Spec] = {
    "plan_cell": Spec(AppPlanCell, ("article_id", "supplier_id", "date"), ("expression", "qty", "note")),
    "adjustment": Spec(AppAdjustment, ("article_id", "date"), ("expression", "qty", "note")),
    "flag": Spec(AppCellFlag, ("kind", "article_id", "supplier_id", "date"), ("qty", "note")),
    "param_override": Spec(ParamOverride, ("scope", "key1", "key2", "field"), ("value",)),
    "program_scrap": Spec(ProgramScrap, ("program_id", "week_start"), ("rate_pct",)),
    "meta": Spec(AppMeta, ("key",), ("value",)),
}


def spec_of(entity: str) -> Spec:
    """``ref:<table>`` for a reference table (key and columns of its schema), otherwise a planner entity."""
    if entity.startswith("ref:"):
        name = entity[4:]
        schema = schema_of(name)
        return Spec(REF_MODELS[name], tuple(schema.key), tuple(c for c in schema.column_names if c not in schema.key))
    return SPECS[entity]


def _enc(v: Any) -> Any:
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def _dec(model: type, name: str, v: Any) -> Any:
    if v is None:
        return None
    col = model.__table__.columns[name]
    if isinstance(col.type, DateTime):
        return dt.datetime.fromisoformat(v)
    if isinstance(col.type, Date):
        return dt.date.fromisoformat(v)
    return v


def encode_key(key: dict[str, Any]) -> dict[str, Any]:
    return {k: _enc(v) for k, v in key.items()}


def _row(session: Session, spec: Spec, key: dict[str, Any]):
    conds = [getattr(spec.model, k) == _dec(spec.model, k, key[k]) for k in spec.key]
    return session.scalars(select(spec.model).where(*conds)).first()


def snapshot(session: Session, entity: str, key: dict[str, Any]) -> dict[str, Any] | None:
    """The state of the row (its non-key columns), ``None`` when it does not exist."""
    if entity == PDP_ACTIVE:
        v = session.scalars(select(PdpVersion).where(PdpVersion.active.is_(True))).first()
        return {"version_id": v.id if v else None}
    spec = spec_of(entity)
    row = _row(session, spec, key)
    if row is None:
        return None
    return {f: _enc(getattr(row, f)) for f in spec.fields}


def apply_state(session: Session, entity: str, key: dict[str, Any], state: dict[str, Any] | None, user: str) -> None:
    """Write the row back to ``state`` (create / update), delete it when ``state`` is ``None``."""
    if entity == PDP_ACTIVE:
        target = (state or {}).get("version_id")
        version = session.get(PdpVersion, target) if target else None
        if target and version is None:
            raise ActionError(409, "la version de PDP concernée a été supprimée")
        for other in session.scalars(select(PdpVersion).where(PdpVersion.active.is_(True))):
            other.active = False
        if version is not None:
            version.active = True
        return
    spec = spec_of(entity)
    row = _row(session, spec, key)
    if state is None:
        if row is not None:
            session.delete(row)
        return
    if row is None:
        row = spec.model(**{k: _dec(spec.model, k, key[k]) for k in spec.key})
        session.add(row)
    for f in spec.fields:
        setattr(row, f, _dec(spec.model, f, state.get(f)))
    if hasattr(row, "updated_by"):
        row.updated_by = user


def _who(session: Session, entity: str, key: dict[str, Any]) -> str:
    """Who last wrote the row (for « modifié depuis par X »)."""
    if entity == PDP_ACTIVE:
        v = session.scalars(select(PdpVersion).where(PdpVersion.active.is_(True))).first()
        return v.imported_by if v else ""
    row = _row(session, spec_of(entity), key)
    return str(getattr(row, "updated_by", "") or "") if row is not None else ""


# =============================================================================
# Recording
# =============================================================================
def group_of(request: Request | None) -> str:
    return (request.headers.get(GROUP_HEADER, "") if request is not None else "")[:60]


def expose(action: AppAction | None, response: Response | None) -> None:
    """Tell the screen which action the request produced (toast « Annuler »)."""
    if action is None or response is None:
        return
    response.headers["X-Action-Id"] = action.id
    response.headers["X-Action-Label"] = quote(action.label)


class Recorder:
    """Collects the *before* state of the rows a request is about to touch, then (:meth:`done`) reads
    their *after* state and saves the action – nothing is saved when no row changed.

    ``group`` (the ``X-Action-Group`` header) merges the requests of one gesture into one action."""

    def __init__(self, session: Session, user: str, group: str = ""):
        self.session, self.user, self.group = session, user, group or ""
        self._items: dict[tuple[str, str], dict[str, Any]] = {}
        self.action: AppAction | None = None

    def before(self, entity: str, key: dict[str, Any]) -> None:
        key = encode_key(key)
        k = (entity, json.dumps(key, sort_keys=True))
        if k not in self._items:
            self._items[k] = {"entity": entity, "key": key, "before": snapshot(self.session, entity, key)}

    def done(self, kind: str, label: str, article_id: str | None = None, response: Response | None = None,
             group_label: str | None = None) -> AppAction | None:
        """``group_label`` : label of the merged action, with ``{n}`` for its number of rows."""
        self.session.flush()
        items = []
        for it in self._items.values():
            after = snapshot(self.session, it["entity"], it["key"])
            if after != it["before"]:
                items.append({**it, "after": after})
        if not items:
            return None
        action = self._grouped() if self.group else None
        if action is not None:
            merged = {(it["entity"], json.dumps(it["key"], sort_keys=True)): it for it in action.items}
            for it in items:
                k = (it["entity"], json.dumps(it["key"], sort_keys=True))
                if k in merged:
                    merged[k] = {**merged[k], "after": it["after"]}
                else:
                    merged[k] = it
            kept = [it for it in merged.values() if it["after"] != it["before"]]
            action.items_json = json.dumps(kept, default=str)
            action.label = (group_label or label).replace("{n}", str(len(kept)))[:255]
            action.ts = utcnow()
            if article_id != action.article_id:
                action.article_id = None
        else:
            action = AppAction(user=self.user, kind=kind, label=label[:255], article_id=article_id, group_id=self.group,
                               items_json=json.dumps(items, default=str), undoable=True)
            self.session.add(action)
            discard_redo(self.session, self.user)
            purge(self.session)
        self.action = action
        self.session.flush()
        expose(action, response)
        return action

    def _grouped(self) -> AppAction | None:
        since = utcnow() - GROUP_WINDOW
        return self.session.scalars(select(AppAction).where(AppAction.user == self.user, AppAction.group_id == self.group,
                                                            AppAction.state == "applied", AppAction.ts >= since)
                                    .order_by(AppAction.ts.desc())).first()


def record_fixed(session: Session, user: str, kind: str, label: str, article_id: str | None = None,
                 response: Response | None = None) -> AppAction:
    """Journalise a gesture that cannot be undone (it still discards the user's redo stack)."""
    action = AppAction(user=user, kind=kind, label=label[:255], article_id=article_id, undoable=False)
    session.add(action)
    discard_redo(session, user)
    purge(session)
    session.flush()
    expose(action, response)
    return action


def discard_redo(session: Session, user: str) -> int:
    rows = session.scalars(select(AppAction).where(AppAction.user == user, AppAction.state == "undone")).all()
    for a in rows:
        a.state = "discarded"
    return len(rows)


def purge(session: Session) -> int:
    limit = utcnow() - dt.timedelta(days=RETENTION_DAYS)
    rows = session.scalars(select(AppAction).where(AppAction.ts < limit)).all()
    for a in rows:
        session.delete(a)
    return len(rows)


# =============================================================================
# Undo / redo
# =============================================================================
def _expired(action: AppAction) -> bool:
    return action.ts < utcnow() - dt.timedelta(days=RETENTION_DAYS)


def _authorize(action: AppAction, actor: str, is_admin: bool) -> None:
    if action.user != actor and not is_admin:
        raise ActionError(403, f"Cette action est celle de {action.user} : seul un administrateur peut l'annuler")


def _sig(it: dict[str, Any]) -> tuple[str, str]:
    return (it["entity"], json.dumps(it["key"], sort_keys=True))


def _conflict(session: Session, action: AppAction, expect: str, skip: set[tuple[str, str]] = frozenset()) -> str | None:
    """« modifié depuis par X » when a touched row (not in ``skip``) is no longer in the ``expect`` state."""
    for it in action.items:
        if _sig(it) in skip:
            continue
        if snapshot(session, it["entity"], it["key"]) != it[expect]:
            who = _who(session, it["entity"], it["key"])
            return f"modifié depuis par {who}" if who else "modifié depuis"
    return None


def conflicts_for(session: Session, users: set[str]) -> dict[str, str | None]:
    """The conflict message of every action of ``users`` (7 days), chain-aware : a row rewritten by a
    newer applied action of the same user is checked by that newer action only (undoing « jusqu'ici »
    restores it first) ; same thing for the older undone actions on the redo side."""
    out: dict[str, str | None] = {}
    for user in users:
        rows = session.scalars(select(AppAction).where(AppAction.user == user, AppAction.undoable.is_(True),
                                                       AppAction.state != "discarded")
                               .order_by(AppAction.ts.desc(), AppAction.id.desc())).all()
        applied = [a for a in rows if a.state == "applied"]           # newest first : the undo order
        undone = [a for a in rows if a.state == "undone"][::-1]       # oldest first : the redo order
        for seq, expect in ((applied, "after"), (undone, "before")):
            seen: set[tuple[str, str]] = set()
            for a in seq:
                out[a.id] = _conflict(session, a, expect, seen)
                seen.update(_sig(it) for it in a.items)
    return out


def conflict_of(session: Session, action: AppAction) -> str | None:
    """« modifié depuis par X » for one action, in its user's chain (see :func:`conflicts_for`)."""
    if not action.undoable or action.state == "discarded":
        return None
    return conflicts_for(session, {action.user}).get(action.id)


def _check(session: Session, action: AppAction, expect: str) -> None:
    for it in action.items:
        if snapshot(session, it["entity"], it["key"]) != it[expect]:
            who = _who(session, it["entity"], it["key"])
            raise ActionError(409, f"« {action.label} » : modifié depuis" + (f" par {who}" if who else "")
                              + " ; rien n'a été changé")


def undo_chain(session: Session, action: AppAction) -> list[AppAction]:
    """The applied actions of the same user newer than ``action`` (newest first), then ``action``."""
    newer = session.scalars(select(AppAction).where(AppAction.user == action.user, AppAction.state == "applied",
                                                    AppAction.undoable.is_(True), AppAction.ts >= action.ts,
                                                    AppAction.id != action.id)
                            .order_by(AppAction.ts.desc(), AppAction.id.desc())).all()
    return [*newer, action]


def redo_chain(session: Session, action: AppAction) -> list[AppAction]:
    """The undone actions of the same user older than ``action`` (oldest first), then ``action``."""
    older = session.scalars(select(AppAction).where(AppAction.user == action.user, AppAction.state == "undone",
                                                    AppAction.ts <= action.ts, AppAction.id != action.id)
                            .order_by(AppAction.ts.asc(), AppAction.id.asc())).all()
    return [*older, action]


def _get(session: Session, action_id: str) -> AppAction:
    action = session.get(AppAction, action_id)
    if action is None:
        raise ActionError(404, "Action inconnue ou trop ancienne (les actions sont gardées 7 jours)")
    return action


def undo(session: Session, actor: str, action_id: str, is_admin: bool = False) -> list[AppAction]:
    """Undo ``action_id`` – and first every newer applied action of the same user.  All or nothing :
    a conflict on any of them raises before anything is written."""
    action = _get(session, action_id)
    _authorize(action, actor, is_admin)
    if not action.undoable:
        raise ActionError(409, f"« {action.label} » ne peut pas être annulée")
    if action.state != "applied":
        raise ActionError(409, f"« {action.label} » est déjà annulée")
    if _expired(action):
        raise ActionError(409, f"« {action.label} » date de plus de {RETENTION_DAYS} jours")
    done = []
    for a in undo_chain(session, action):
        _check(session, a, "after")
        for it in reversed(a.items):
            apply_state(session, it["entity"], it["key"], it["before"], actor)
        a.state, a.changed_at, a.changed_by = "undone", utcnow(), actor
        audit(session, actor, "undo", "action", a.id, a.article_id, {"label": a.label, "user": a.user, "items": len(a.items)})
        session.flush()
        done.append(a)
    return done


def redo(session: Session, actor: str, action_id: str, is_admin: bool = False) -> list[AppAction]:
    """Redo ``action_id`` – and first every older undone action of the same user."""
    action = _get(session, action_id)
    _authorize(action, actor, is_admin)
    if action.state == "discarded":
        raise ActionError(409, f"« {action.label} » ne peut plus être rétablie : une autre action a été faite depuis")
    if action.state != "undone":
        raise ActionError(409, f"« {action.label} » n'est pas annulée")
    done = []
    for a in redo_chain(session, action):
        _check(session, a, "before")
        for it in a.items:
            apply_state(session, it["entity"], it["key"], it["after"], actor)
        a.state, a.changed_at, a.changed_by = "applied", utcnow(), actor
        audit(session, actor, "redo", "action", a.id, a.article_id, {"label": a.label, "user": a.user, "items": len(a.items)})
        session.flush()
        done.append(a)
    return done


def last_undoable(session: Session, user: str) -> AppAction | None:
    since = utcnow() - dt.timedelta(days=RETENTION_DAYS)
    return session.scalars(select(AppAction).where(AppAction.user == user, AppAction.state == "applied",
                                                   AppAction.undoable.is_(True), AppAction.ts >= since)
                           .order_by(AppAction.ts.desc(), AppAction.id.desc())).first()


def first_redoable(session: Session, user: str) -> AppAction | None:
    return session.scalars(select(AppAction).where(AppAction.user == user, AppAction.state == "undone")
                           .order_by(AppAction.ts.asc(), AppAction.id.asc())).first()


def undo_last(session: Session, actor: str) -> list[AppAction]:
    action = last_undoable(session, actor)
    if action is None:
        raise ActionError(409, "Rien à annuler")
    return undo(session, actor, action.id)


def redo_last(session: Session, actor: str) -> list[AppAction]:
    action = first_redoable(session, actor)
    if action is None:
        raise ActionError(409, "Rien à rétablir")
    return redo(session, actor, action.id)


# =============================================================================
# Listing
# =============================================================================
CELL_ENTITIES = ("plan_cell", "adjustment", "flag")


def touched_cells(actions: list[AppAction]) -> list[dict[str, Any]]:
    """The grid cells the actions touched (the screen highlights them after an undo / redo)."""
    out, seen = [], set()
    for a in actions:
        for it in a.items:
            if it["entity"] not in CELL_ENTITIES:
                continue
            k = it["key"]
            cell = {"entity": it["entity"], "article_id": k.get("article_id"), "supplier_id": k.get("supplier_id") or None, "date": k.get("date")}
            sig = tuple(cell.values())
            if sig not in seen:
                seen.add(sig)
                out.append(cell)
    return out


def to_out(session: Session, action: AppAction, actor: str, is_admin: bool, conflict: str | None = None) -> dict[str, Any]:
    mine = action.user == actor
    return {"id": action.id, "ts": action.ts, "user": action.user, "kind": action.kind, "label": action.label,
            "article_id": action.article_id, "group_id": action.group_id, "items": len(action.items),
            "state": action.state, "undoable": action.undoable, "changed_at": action.changed_at,
            "changed_by": action.changed_by, "mine": mine, "can_act": (mine or is_admin) and action.undoable and not _expired(action),
            "conflict": conflict}


def list_actions(session: Session, actor: str, is_admin: bool, user: str | None = None, article_id: str | None = None,
                 mine: bool = False, limit: int = 50) -> list[dict[str, Any]]:
    purge(session)
    q = select(AppAction).order_by(AppAction.ts.desc(), AppAction.id.desc()).limit(max(1, min(limit, 500)))
    if mine:
        q = q.where(AppAction.user == actor)
    elif user:
        q = q.where(AppAction.user == user)
    if article_id:
        q = q.where(AppAction.article_id == article_id)
    rows = session.scalars(q).all()
    conflicts = conflicts_for(session, {a.user for a in rows})
    return [to_out(session, a, actor, is_admin, conflicts.get(a.id)) for a in rows]
