"""The existing research controller owns archive, review and publication."""
from __future__ import annotations

import json
from pathlib import Path

from ..materials import now
from ..schemas import ToolResponse
from .preferences import Preferences
from .schemas import KnowledgeItem, KnowledgeQuery, KnowledgeReview, ProposalArguments, ReadKnowledge, digest
from .service import KnowledgeService
from .store import KnowledgeStore, VersionConflict, atomic_bytes


CONTRACTS = {
    "knowledge_search": {"description": "Hard metadata/cutoff filters, Chinese FTS5 and budgeted embeddings. Similarity is not verification. Historical theses are reference only.", "arguments_schema": KnowledgeQuery.model_json_schema()},
    "knowledge_read": {"description": "Read a fixed entry/revision; adopt only an applicable independently verified historical fact. Returns original source hash/locator and review records.", "arguments_schema": ReadKnowledge.model_json_schema()},
    "knowledge_propose": {"description": "Propose a fact, thesis, question or status change with company base version and original references. Cannot self-approve or commit.", "arguments_schema": ProposalArguments.model_json_schema()},
    "knowledge_review": {"description": "Independent verifier only. Review exact proposal content hash, source, financial scope, assumptions and counter-evidence. Question approval never certifies an unknown answer.", "arguments_schema": KnowledgeReview.model_json_schema()},
}


class ResearchKnowledge:
    def __init__(self, engine):
        self.engine = engine
        self.store = KnowledgeStore(engine.state["knowledge"]["root"], engine._check_adapter_budget)
        with self.store.connect() as db:
            db.execute("INSERT OR IGNORE INTO sessions(id,directory) VALUES(?,?)",(engine.state["run_id"],str(engine.directory.resolve())))
        self.refresh()
        engine.tools.adapter.checkpoint_store = self.store
        engine.tools.adapter.cutoff = engine.state["research"]["cutoff"]
        engine.tools.adapter.cached_parse = self.cached_parse

    def cached_parse(self, source_hash):
        try:
            source, _ = self.store.source("src-"+source_hash[:20])
        except ValueError:
            return None
        fingerprint = digest(self.engine.state.get("capabilities",{}).get("models",{}).get("ocr"))
        if source.get("parser_version") == "raw-v3" and source.get("ocr_fingerprint") == fingerprint:
            return json.loads(json.dumps(source["chunks"])),list(source["gaps"])
        return None

    def refresh(self):
        state = self.engine.state
        research = dict(state["research"]) | {"knowledge_security_id": state["knowledge"].get("security_id")}
        self.service = KnowledgeService(self.store, state["run_id"], research, self.engine.run_budget,
                                       state.get("capabilities", {}).get("models", {}).get("embedding"))

    def initialize(self):
        state, meta = self.engine.state, self.engine.state["knowledge"]
        company, security = self.store.identity(state["research"], state["run_id"])
        meta.update(company_id=company, security_id=security)
        self.engine.tools.adapter.target_id=company
        self.refresh()

    def ref(self, anchor):
        pair = self.engine.tools.anchors().get(anchor)
        if not pair:
            raise ValueError("knowledge evidence must resolve in current session")
        chunk, source = pair
        return {"source_id": source["id"], "source_hash": source["source_hash"], "anchor_ref": anchor, "locator": chunk["location"]}

    def item(self, kind, content, refs=(), retention=None, **fields):
        meta = self.engine.state["knowledge"]
        return KnowledgeItem(company_id=meta["company_id"], security_id=meta.get("security_id"), kind=kind,
                             content=content, evidence=[self.ref(r) for r in refs], retention=retention or {},
                             topics=self.engine.state["research"].get("topics", []),research_cutoff=self.engine.state["research"]["cutoff"], **fields).model_dump(mode="json")

    def archive_item(self, item, proposer="controller:archive"):
        meta, session = self.engine.state["knowledge"], self.engine.state["run_id"]
        key = digest(item)
        if key in meta["archived"]:
            return meta["archived"][key]
        proposal = self.store.propose(item, session, proposer, self.store.version(item["company_id"]))
        try:
            record = self.store.commit(proposal["id"], allow_pending=True)
        except VersionConflict:
            self.store.log("archive_version_conflict", session, {"proposal_id": proposal["id"]})
            return None
        fixed = {"entry_id": record["entry_id"], "revision_id": record["id"], "proposal_id": proposal["id"]}
        meta["archived"][key] = fixed
        meta["proposals"].append(proposal["id"])
        return fixed

    def archive_sources(self, closing=False):
        # Closing archives acquired bytes without starting new model or network calls.
        check = self.store.check
        if closing:
            self.store.check = lambda: None
        try:
            for entry in self.engine.tools.load_manifest()["materials"]:
                entry = entry | {"parser_version":"raw-v3","ocr_fingerprint":digest(self.engine.state.get("capabilities",{}).get("models",{}).get("ocr"))}
                self.store.archive_source(entry, (self.engine.directory / "materials" / entry["file"]).read_bytes(), self.engine.state["run_id"])
                for channel in entry.get("acquisitions", []):
                    self.store.archive_source(entry | channel | {"id": entry["id"], "source_hash": entry["source_hash"], "chunks": entry["chunks"]}, (self.engine.directory / "materials" / entry["file"]).read_bytes(), self.engine.state["run_id"])
                for chunk in entry["chunks"]:
                    item = self.item("source", {"text": chunk["text"], "title": entry.get("title"), "url": entry.get("url"), "locator": chunk["location"]}, [chunk["ref"]],
                                     disclosed_at=entry.get("date"), disclosure_basis=entry.get("date_basis"))
                    self.archive_item(item)
        finally:
            self.store.check = check

    def archive_extraction(self):
        self.archive_sources()
        if not self.engine.tools.extraction_path.exists():
            return
        extracted = json.loads(self.engine.tools.extraction_path.read_text())
        for row in extracted["financials"] + extracted["evidence"]:
            related = {}
            if row in [v for conflict in extracted.get("conflicts", []) for v in conflict]:
                related["conflicts"] = [digest(c) for c in extracted["conflicts"] if row in c]
            self.archive_item(self.item("fact", row, [row["source_ref"]], period=row.get("period"), disclosed_at=row.get("disclosure_date"),
                                        disclosure_basis=row.get("disclosure_source_ref"), relations=related))

    def archive_thesis(self, result):
        data = result["data"]
        for claim in data.get("claims", []) + data.get("hypotheses", []):
            refs = [r for r in claim.get("evidence_refs", []) if r in self.engine.tools.anchors()]
            item = self.item("thesis", claim, refs, {"R1": "本次研究关键论点的支持或反驳线索"}, formed_at=now(),
                             assumptions=[str(x) for x in claim.get("assumptions", [])],
                             invalidation_conditions=[str(x) for x in claim.get("invalidation_conditions", data.get("disconfirming", []))])
            opponents = claim.get("opposing_evidence_refs", [])
            item["opposing_evidence"] = [self.ref(r) for r in opponents if r in self.engine.tools.anchors()]
            self.archive_item(item, result["task_id"])
            self.prioritize_refs(refs,"R1","原文位置被本次关键投资论点引用："+str(claim.get("statement","")))
        for question in data.get("tracking", []) + data.get("gaps", []):
            refs = [r for r in result.get("evidence_refs", []) if r in self.engine.tools.anchors()]
            self.archive_item(self.item("question", {"question": question, "answer": None}, refs,
                                        {"R3": "当前研究待核查问题，批准仅表示核查价值"}, formed_at=now()), result["task_id"])

    def prioritize_refs(self,refs,rule,reason):
        for fixed in self.engine.state["knowledge"]["archived"].values():
            record=self.store.read(fixed["entry_id"],fixed["revision_id"])
            if record["item"]["kind"]=="fact" and any(e["anchor_ref"] in refs for e in record["item"]["evidence"]):
                self.store.mark_priority(record["id"],rule,reason,self.engine.state["run_id"])

    def startup(self):
        self.refresh()
        meta, research = self.engine.state["knowledge"], self.engine.state["research"]
        company = meta["company_id"]
        loaded = self.service.search({"company_ids": [company], "cutoff": research["cutoff"], "context_bytes": 18000,"summary_only":True})
        # Only priority records auto-load; all raw sources remain discoverable.
        summary = [r for r in loaded["results"] if r["item"]["retention"] or r.get("priority") or r["item"]["kind"] == "source" or r["applicability"]["reusable_fact"]]
        remaining = max(1, 24000-len(json.dumps(summary, ensure_ascii=False).encode()))
        query = " ".join(research.get("questions", []))
        if query:
            related = self.service.search({"query": query, "cutoff": research["cutoff"], "context_bytes": remaining})
            seen = {r["id"] for r in summary}
            summary.extend(r for r in related["results"] if r["id"] not in seen)
        meta["startup"] = summary
        meta["startup_notice"] = "历史论点仅供参考；本次结论必须形成新判断并独立核验。仍需主动检查新披露、反证及市场基准。"
        self.reuse([r for r in summary if r["applicability"]["reusable_fact"]])

    def reuse(self, rows):
        from ..knowledge.store import atomic_bytes
        from ..schemas import ReadArguments
        engine, reused = self.engine, []
        for row in rows:
            item = row["item"]
            if (item["content"].get("information_kind") or item["content"].get("kind") or "fact") != "fact":
                continue
            for ref in item["evidence"]:
                source, path = self.store.source(ref["source_id"])
                index = engine.tools.load_manifest()
                existing = next((x for x in index["materials"] if x["id"] == source["id"]), None)
                if not existing:
                    atomic_bytes(engine.directory / "materials" / source["file"], path.read_bytes())
                    index["materials"].append(source)
                    engine.tools.adapter._write(index)
                self.store.pin(engine.state["run_id"], row["id"], True)
            reused.append(item["content"])
        if reused:
            aggregate = json.loads(engine.tools.extraction_path.read_text()) if engine.tools.extraction_path.exists() else {"financials": [], "evidence": [], "gaps": []}
            changed=False
            for content in reused:
                key = "financials" if "field" in content else "evidence"
                if content not in aggregate[key]:
                    aggregate[key].append(content)
                    changed=True
            engine.tools.accept_extraction(aggregate, engine.state["research"]["cutoff"])
            engine.state["knowledge"]["reused"] = sorted(set(engine.state["knowledge"]["reused"])|{r["id"] for r in rows})
            engine.state["input_snapshot"] = engine.tools.snapshot()
            if changed and engine.state["artifacts"].get("finance"):
                from ..schemas import Assumptions
                for key in ("finance","thesis","proposal","valuation","verification"):
                    old=engine.state["artifacts"].pop(key,None)
                    if old:
                        engine.state["invalidated"].append(old["task_id"])
                engine.state["assumption_history"].append(dict(engine.state["assumptions"]))
                engine.state["assumptions"]=Assumptions(version=engine.state["assumptions"]["version"]+1).model_dump()
                engine._log("historical_fact_adopted_artifacts_invalidated",{"revision_ids":[r["id"] for r in rows]})

    def execute(self, name, args, task):
        self.refresh()
        try:
            if name == "knowledge_search":
                args = KnowledgeQuery.model_validate(args).model_dump()
                args["cutoff"] = task.cutoff
                data = self.service.search(args, task.target)
            elif name == "knowledge_read":
                parsed = ReadKnowledge.model_validate(args)
                data = self.service.read(parsed.entry_id, parsed.revision_id, task.target, parsed.adopt,parsed.max_content_bytes)
                if parsed.adopt:
                    self.reuse([data])
            elif name == "knowledge_propose":
                parsed = ProposalArguments.model_validate(args)
                if task.target == "verification":
                    raise PermissionError("verifier cannot originate and approve the same proposal")
                if parsed.item.company_id != self.engine.state["knowledge"]["company_id"]:
                    raise PermissionError("knowledge write outside target company")
                parsed.item.research_cutoff=task.cutoff
                data = self.store.propose(parsed.item.model_dump(), self.engine.state["run_id"], task.task_id, parsed.base_version, parsed.entry_id)
                self.engine.state["knowledge"]["proposals"].append(data["id"])
            else:
                data = self.service.review(args, task.task_id, task.target)
            return ToolResponse(ok=True, data=data).model_dump()
        except (ValueError, PermissionError) as exc:
            return ToolResponse(ok=False, error={"code": "KNOWLEDGE", "message": str(exc), "retryable": False}).model_dump()

    def commit_reviewed(self):
        meta = self.engine.state["knowledge"]
        meta["submission_issues"]=[]
        for identifier in meta["proposals"]:
            proposal = self.store.proposal(identifier)
            if proposal["revision_id"]:
                continue
            try:
                self.store.commit(identifier)
            except (PermissionError, VersionConflict) as exc:
                problem={"proposal_id":identifier,"reason":str(exc),"base_version":proposal["base"],"current_version":self.store.version(proposal["company_id"])}
                meta["submission_issues"].append(problem)
                self.store.log("knowledge_commit_refused", self.engine.state["run_id"], problem)

    def fixed_refs(self):
        if self.engine.state.get("mode") == "offline-replay":
            return self.engine.state["knowledge"]["fixed_refs"]
        with self.store.connect() as db:
            rows = db.execute("SELECT r.entry_id,r.id,p.adopted FROM pins p JOIN revisions r ON p.revision_id=r.id WHERE p.session=?", (self.engine.state["run_id"],)).fetchall()
        return [dict(r) for r in rows]

    def finish(self):
        if self.engine.state.get("mode") == "offline-replay":
            self.store.log("fixed_knowledge_replay",self.engine.state["run_id"],{"source_session":self.engine.state["replay_source"],"fixed_refs":self.fixed_refs(),"remote_calls":0})
            return
        if (self.engine.directory/"source-missing.json").exists():
            self.store.log("session_archived_source_missing",self.engine.state["run_id"],{"status":self.engine.state["status"],"cannot_fully_reproduce":True})
            return
        self.archive_sources(closing=True)
        check = self.store.check
        self.store.check = lambda: None
        try:
            if self.engine.tools.extraction_path.exists():
                extracted = json.loads(self.engine.tools.extraction_path.read_text())
                for row in extracted["financials"]+extracted["evidence"]:
                    self.archive_item(self.item("fact",row,[row["source_ref"]],period=row.get("period"),disclosed_at=row.get("disclosure_date")))
        finally:
            self.store.check = check
        # Retain full outputs alongside SQLite authoritative study metadata.
        engine = self.engine
        path = self.store.root / "reports" / engine.state["run_id"]
        path.mkdir(parents=True, exist_ok=True)
        files=[]
        import hashlib
        for report in engine.directory.glob("report-v*.*"):
            content=report.read_bytes()
            source_hash=hashlib.sha256(content).hexdigest()
            destination=path/("report-"+source_hash+report.suffix)
            atomic_bytes(destination,content)
            files.append({"path":str(destination.relative_to(self.store.root)),"hash":source_hash,"format":report.suffix})
        check=self.store.check
        self.store.check=lambda:None
        try:
            refs=[chunk["ref"] for source in engine.tools.load_manifest()["materials"] for chunk in source["chunks"]]
            self.archive_item(self.item("study",{"run_id":engine.state["run_id"],"status":engine.state["status"],"reports":files},refs,formed_at=now()))
        finally:
            self.store.check=check
        self.store.log("session_archived", engine.state["run_id"], {"status": engine.state["status"], "report_path": str(path), "verification_unchanged": True})
        engine.state["knowledge"]["fixed_refs"] = self.fixed_refs()
