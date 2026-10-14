"""Explicit persistent requirements, revision history and per-session application."""
from __future__ import annotations

import json
from uuid import uuid4

from ..materials import now


ALLOWED_DEFAULTS = {"horizon", "framework", "questions", "topics", "notes", "authorization", "confirmation_mode"}
SETUP_ID = "research-setup"


class SetupRequired(ValueError):
    code = "research_setup_required"


class Preferences:
    def __init__(self, store):
        self.store = store

    def save(self, original, meaning, parameters, scope="global", source="cli:/remember", identifier=None):
        if not original.strip() or not meaning.strip() or not isinstance(parameters, dict) or set(parameters) - ALLOWED_DEFAULTS:
            raise ValueError("explicit requirement needs original wording, meaning and supported defaults; hard constraints cannot be overridden")
        if "horizon" in parameters and (not isinstance(parameters["horizon"], str) or not parameters["horizon"].strip()):
            raise ValueError("horizon must be nonempty text")
        if "framework" in parameters and parameters["framework"] not in {"auto", "quality", "value", "change"}:
            raise ValueError("framework default must name a built-in verified framework")
        if any(k in parameters and (not isinstance(parameters[k], list) or not all(isinstance(v, str) for v in parameters[k])) for k in ("questions", "topics")):
            raise ValueError("questions/topics must be text arrays")
        if "notes" in parameters and not isinstance(parameters["notes"], str):
            raise ValueError("requirement notes must be text")
        if "authorization" in parameters or "confirmation_mode" in parameters or identifier == SETUP_ID:
            from ..schemas import ResearchSetup
            if identifier != SETUP_ID or scope != "global":
                raise ValueError("authorization is recorded only by preferences setup")
            parameters = ResearchSetup.model_validate(parameters).model_dump()
        identifier = identifier or "preference-" + uuid4().hex
        with self.store.transaction() as db:
            if scope != "global" and not db.execute("SELECT 1 FROM companies WHERE id=?", (scope,)).fetchone():
                raise ValueError("specific company scope requires an unambiguous company ID")
            prior = db.execute("SELECT * FROM preferences WHERE id=? ORDER BY version DESC LIMIT 1", (identifier,)).fetchone()
            if prior and prior["scope"] != scope:
                raise ValueError("preference update cannot silently change scope")
            version = prior["version"]+1 if prior else 1
            db.execute("INSERT INTO preferences VALUES(?,?,?,?,?,?,?,?,?)", (identifier, version, scope, original, meaning, json.dumps(parameters, ensure_ascii=False), source, 1, now()))
            self.store.log("remember", "user", {"id": identifier, "version": version, "scope": scope, "meaning": meaning}, db)
        return {"id": identifier, "version": version, "scope": scope, "meaning": meaning, "parameters": parameters, "saved": True, "application": "pending"}

    def setup(self, parameters, source="cli:preferences setup"):
        from ..schemas import ResearchSetup
        values = ResearchSetup.model_validate(parameters).model_dump()
        return self.save(json.dumps(values, ensure_ascii=False), "明确研究偏好及自主选择授权", values,
                         source=source, identifier=SETUP_ID)

    def setup_snapshot(self):
        row = next((p for p in self.list(history=True) if p["id"] == SETUP_ID), None)
        if row is None:
            raise SetupRequired("首次研究前必须完成 preferences setup，并明确选择授权或拒绝授权")
        result = {k: row[k] for k in ("id", "version", "parameters", "active", "recorded_at", "source")}
        if not row["active"]:
            result["parameters"] = result["parameters"] | {"authorization": {"status": "refused", "scope": []}}
        return result

    def research_snapshot(self):
        """Use a per-run policy when no user choice exists; never save consent."""
        try:
            return self.setup_snapshot()
        except SetupRequired:
            return {"id": None, "version": 0, "active": False, "recorded_at": None,
                    "source": "run_default", "parameters": {
                        "horizon": "12个月", "framework": "auto", "confirmation_mode": "autonomous",
                        "authorization": {"status": "unspecified", "scope": []},
                        "questions": [], "topics": [], "notes": ""}}

    def list(self, history=False):
        with self.store.connect() as db:
            rows = db.execute("SELECT * FROM preferences ORDER BY version DESC").fetchall()
        if not history:
            latest = {}
            for row in rows:
                latest.setdefault(row["id"], row)
            rows = [r for r in latest.values() if r["active"]]
        return [dict(r) | {"parameters": json.loads(r["parameters"])} for r in rows]

    def revoke(self, identifier):
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM preferences WHERE id=? ORDER BY version DESC LIMIT 1", (identifier,)).fetchone()
            if not row:
                raise ValueError("unknown requirement")
            db.execute("INSERT INTO preferences VALUES(?,?,?,?,?,?,?,?,?)", (row["id"], row["version"]+1, row["scope"], row["original"], row["meaning"], row["parameters"], "cli:/forget", 0, now()))
            self.store.log("forget", "user", {"id": identifier, "version": row["version"]+1}, db)
        return {"revoked": identifier, "history_retained": True}

    def resolve(self, company, explicit, defaults, session):
        result, applied = dict(defaults), []
        preferences = sorted((r for r in self.list() if r["scope"] in {"global", company}), key=lambda r: (r["scope"] != "global", r["recorded_at"], r["id"]))
        for preference in preferences:
            result.update(preference["parameters"])
        result.update(explicit)
        for preference in preferences:
            blocked = sorted(set(preference["parameters"]) & set(explicit))
            record = {"id": preference["id"], "version": preference["version"], "scope": preference["scope"], "status": "applied_with_explicit_overrides" if blocked else "applied", "explicit_overrides": blocked}
            with self.store.connect() as db:
                db.execute("INSERT INTO applications VALUES(?,?,?,?,?,?)", (session, preference["id"], preference["version"], record["status"], json.dumps(record), now()))
            applied.append(record)
        return result, applied
