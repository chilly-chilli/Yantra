"""Permission gates, temporal retrieval, applicability and fixed session reads."""
from __future__ import annotations

import json
import sqlite3

from ..materials import cutoff_time, now, source_number_present, numeric
from .embedding import INDEX_VERSION, Embedder, cosine, vector_text
from .schemas import KnowledgeItem, KnowledgeQuery, digest
from .store import terms, search_expression


class KnowledgeService:
    def __init__(self, store, session, research, budget=None, embedding=None):
        self.store, self.session, self.research, self.budget = store, session, research, budget
        self.embedder = Embedder(embedding, budget) if embedding and budget else None
        self.degradations=[]

    def check(self, role):
        if role not in {"main", "finance", "thesis", "valuation", "verification", "cli"}:
            raise PermissionError("knowledge role not authorized")
        if self.budget:
            self.budget.check()

    def available(self, item, cutoff, recorded_at=None):
        if not cutoff:
            return True
        limit = cutoff_time(cutoff)
        if item.get("disclosed_at") and cutoff_time(item["disclosed_at"]) > limit:
            return False
        if item["kind"] in {"thesis", "question", "study"} and (not item.get("formed_at") or cutoff_time(item["formed_at"]) > limit):
            return False
        if item["kind"] in {"thesis","question","study"} and recorded_at and cutoff_time(recorded_at)>limit:
            return False
        for ref in item["evidence"] + item["opposing_evidence"]:
            try:
                source, _ = self.store.source(ref["source_id"])
            except ValueError:
                return False
            date = source.get("date")
            if date and cutoff_time(date) > limit:
                return False
            if not date and limit.date() < cutoff_time(source["acquired_at"]).date():
                return False
        return True

    def applicability(self, record, query=None):
        item, reasons = record["item"], []
        cutoff = (query.cutoff if query else None) or self.research.get("cutoff")
        if not self.available(item, cutoff, record.get("recorded_at")):
            reasons.append("outside information cutoff")
        try:
            self.store.validate_evidence(KnowledgeItem.model_validate(item))
        except ValueError:
            reasons.append("source missing/deleted/hash mismatch")
        if query and query.period and item["period"] != query.period:
            reasons.append("report period mismatch")
        security = self.research.get("knowledge_security_id")
        if security and item.get("security_id") != security:
            reasons.append("security identity requires re-verification")
        content = item["content"]
        if item["kind"] == "fact" and "field" in content:
            if not all(content.get(k) is not None for k in ("period", "currency", "unit", "consolidated", "security_code", "share_class", "accounting_scope")):
                reasons.append("original verification lacks identity/financial scope")
            for key, expected in (("currency", self.research.get("fact_currency")), ("unit", self.research.get("fact_unit")), ("accounting_scope", self.research.get("accounting_scope"))):
                if expected and content.get(key) != expected:
                    reasons.append(key + " mismatch")
        if item.get("relations", {}).get("conflicts"):
            reasons.append("unresolved conflict; re-verification required")
        content_key = self.fact_key(item)
        if content_key:
            candidates = self.rows(KnowledgeQuery(company_ids=[item["company_id"]], cutoff=cutoff, kinds=["fact"]))
            if any(self.fact_key(other["item"]) == content_key and other["item"]["content"].get("value") != item["content"].get("value") for other in candidates):
                reasons.append("same-period/same-scope conflicting values; no newest-value arbitration")
                self.store.mark_priority(record["id"],"R4","同期间与口径出现不同数值，需要调节冲突",self.session)
        if item.get("relations", {}).get("superseded_by"):
            reasons.append("corrected disclosure; re-verification required")
        if item["kind"]=="fact":
            candidates=self.rows(KnowledgeQuery(company_ids=[item["company_id"]],cutoff=cutoff,kinds=["fact"]))
            if any({record["entry_id"],record["id"]}&set(other["item"]["relations"].get("correction_of",[])) for other in candidates):
                reasons.append("correction candidate references this revision; re-verification required")
        verified = record["verification"]["status"] == "passed"
        reusable = item["kind"] == "fact" and verified and not reasons and not record["deleted"]
        return {"reusable_fact": reusable, "formal_current_conclusion": False,
                "category": "historical_judgment_reference" if item["kind"] == "thesis" else "manual_input" if item["manual"] else "verified_fact" if reusable else "unverified_candidate",
                "reasons": reasons, "requires_current_acceptance": True}

    @staticmethod
    def fact_key(item):
        content = item["content"]
        if item["kind"] != "fact" or "field" not in content:
            return None
        return tuple(content.get(k) for k in ("field","period","currency","unit","consolidated","security_code","share_class","accounting_scope","business_scope"))

    def rows(self, query):
        with self.store.connect() as db:
            rows = db.execute("SELECT v.*,e.deleted FROM revisions v JOIN entries e ON e.id=v.entry_id WHERE e.deleted=0 ORDER BY v.company_version DESC").fetchall()
        chosen = {}
        for row in rows:
            item = json.loads(row["body"])
            if row["entry_id"] in chosen or item["usage"] == "session" and row["session"] != self.session:
                continue
            if not self.available(item, query.cutoff or self.research.get("cutoff"), row["recorded_at"]):
                continue
            # Select the latest available revision before applying its metadata.
            chosen[row["entry_id"]] = dict(row) | {"item": item}
        result = []
        for row in chosen.values():
            item = row["item"]
            if query.company_ids is not None and item["company_id"] not in query.company_ids:
                continue
            if query.security_id and item["security_id"] != query.security_id:
                continue
            if query.period and item["period"] != query.period:
                continue
            if query.kinds and item["kind"] not in query.kinds:
                continue
            if query.topics and not set(query.topics) & set(item["topics"]):
                continue
            try:
                self.store.validate_evidence(KnowledgeItem.model_validate(item))
            except ValueError:
                continue
            with self.store.connect() as db:
                row["verification"] = self.store.verification(row["id"], db)
            row["priority"] = self.store.priority(row["id"])
            result.append(row)
        return result

    def degradation(self, reason):
        self.degradations.append(reason)
        self.store.log("embedding_degraded", self.session, {"reason": reason})

    def index_vectors(self, rows, force=False):
        if not self.embedder:
            self.degradation("embedding not configured")
            return 0
        if not self.embedder.profile.get("pricing"):
            self.degradation("embedding pricing unknown; remote calls disabled")
            return 0
        count = 0
        for row in rows:
            try:
                self.check("main")
                with self.store.connect() as db:
                    old = db.execute("SELECT 1 FROM vectors WHERE revision_id=? AND fingerprint=?", (row["id"], self.embedder.fingerprint)).fetchone()
                    if old and not force:
                        continue
                    # Formatting-only revisions can reuse identical semantic content.
                    text=vector_text(row["item"])
                    content_hash=digest(text)
                    reusable = db.execute("SELECT vector,dimension FROM vector_cache WHERE content_hash=? AND fingerprint=? AND index_version=?", (content_hash, self.embedder.fingerprint,INDEX_VERSION)).fetchone()
                vector = json.loads(reusable["vector"]) if reusable and not force else self.embedder.embed([text])[0]
                with self.store.connect() as db:
                    db.execute("INSERT OR REPLACE INTO vectors VALUES(?,?,?,?,?,?)", (row["id"], self.embedder.fingerprint, self.embedder.profile["model_id"], len(vector), INDEX_VERSION, json.dumps(vector)))
                    db.execute("INSERT OR REPLACE INTO vector_cache VALUES(?,?,?,?,?)",(content_hash,self.embedder.fingerprint,len(vector),json.dumps(vector),INDEX_VERSION))
                count += 1
            except Exception as exc:
                self.degradation(type(exc).__name__)
                break
        return count

    def search(self, arguments, role="main"):
        self.check(role)
        self.degradations=[]
        query = KnowledgeQuery.model_validate(arguments)
        rows = self.rows(query)
        lexical, semantic = {}, {}
        if query.query:
            expression = search_expression(query.query)
            try:
                with self.store.connect() as db:
                    hits = db.execute("SELECT revision_id,bm25(fulltext) AS score FROM fulltext WHERE fulltext MATCH ? ORDER BY score", (expression,)).fetchall() if expression else []
                lexical = {row["revision_id"]: rank+1 for rank, row in enumerate(hits)}
            except sqlite3.Error:
                self.store.log("fts_degraded", self.session, {"fallback": "normalized keywords"})
                lexical = {row["id"]: i+1 for i, row in enumerate(rows) if any(t in terms(row["body"]).split() for t in terms(query.query).split())}
            if self.embedder:
                self.index_vectors(rows)
                try:
                    vector = self.embedder.embed([query.query])[0]
                    with self.store.connect() as db:
                        saved = {r["revision_id"]: r for r in db.execute("SELECT * FROM vectors WHERE fingerprint=? AND index_version=?", (self.embedder.fingerprint, INDEX_VERSION))}
                    for row in rows:
                        if row["id"] in saved and saved[row["id"]]["dimension"] == len(vector):
                            similarity = cosine(vector, json.loads(saved[row["id"]]["vector"]))
                            if similarity > 0:
                                semantic[row["id"]] = similarity
                except Exception as exc:
                    self.degradation(type(exc).__name__)
            else:
                self.degradation("embedding not configured; metadata/FTS only")
        semantic_rank = {identifier: rank+1 for rank, (identifier, _) in enumerate(sorted(semantic.items(), key=lambda x: -x[1]))}
        hits = []
        for row in rows:
            identifier = row["id"]
            if query.query and identifier not in lexical and identifier not in semantic_rank:
                continue
            score = (1/(60+lexical[identifier]) if identifier in lexical else 0) + (1/(60+semantic_rank[identifier]) if identifier in semantic_rank else 0)
            row["match"] = {"rank_rule": "hard_filters_then_RRF60; retention_tiebreak", "fts_rank": lexical.get(identifier), "cosine": semantic.get(identifier), "score": score}
            row["applicability"] = self.applicability(row, query)
            hits.append(row)
        hits.sort(key=lambda r: (-r["match"]["score"], -bool(r["item"]["retention"] or r["priority"]), r["entry_id"]))
        results, used, deduplicated = [], 0, set()
        for row in hits[:query.limit]:
            independence = digest([row["item"]["kind"],row["item"]["content"],row["item"]["evidence"],row["item"]["judgment"],row["item"]["relations"]])
            if independence in deduplicated:
                continue
            deduplicated.add(independence)
            preview = {k: v for k, v in row.items() if k != "body"}
            if query.summary_only and preview["item"]["kind"] == "source":
                preview["item"] = dict(preview["item"])
                preview["item"]["content"] = {k:v for k,v in preview["item"]["content"].items() if k != "text"}
                preview["item"]["content"]["original_read"] = "knowledge_read with stable entry_id/revision_id"
            preview["evidence_independence_keys"] = sorted({e["source_hash"] for e in row["item"]["evidence"]+row["item"]["opposing_evidence"]})
            size = len(json.dumps(preview, ensure_ascii=False).encode())
            if used + size > query.context_bytes:
                continue
            used += size
            results.append(preview)
            self.store.pin(self.session, row["id"])
        self.store.log("search", self.session, {"query": query.model_dump(), "revisions": [r["id"] for r in results], "context_bytes": used})
        return {"results": results, "candidate_count": len(rows), "context_bytes": used,"degradation":self.degradations, "notice": "similarity is not correctness; historical judgments are reference only"}

    def read(self, entry, revision, role="main", adopt=False,max_content_bytes=64000):
        self.check(role)
        record = self.store.read(entry, revision)
        record["priority"] = self.store.priority(revision)
        item = record["item"]
        with self.store.connect() as db:
            pinned = db.execute("SELECT 1 FROM pins WHERE session=? AND revision_id=?", (self.session, revision)).fetchone()
        if record["deleted"] and not pinned or item["usage"] == "session" and record["session"] != self.session:
            raise PermissionError("knowledge outside session access scope")
        if not pinned and not self.available(item, self.research.get("cutoff"),record["recorded_at"]):
            raise PermissionError("knowledge outside cutoff")
        # Old fixed reads can survive soft deletion; adoption always checks current applicability.
        for ref in item["evidence"] + item["opposing_evidence"]:
            self.store.source(ref["source_id"], allow_deleted=bool(pinned and not adopt))
        application = self.applicability(record)
        if adopt and not application["reusable_fact"]:
            raise ValueError("history requires current independent re-verification")
        self.store.pin(self.session, revision, adopt)
        if item["kind"]=="study":
            import hashlib
            reports=[]
            remaining=max_content_bytes
            for reference in item["content"].get("reports",[]):
                path=(self.store.root/reference["path"]).resolve()
                if not path.is_relative_to(self.store.root) or not path.exists():
                    raise ValueError("archived report missing; cannot reproduce")
                content=path.read_bytes()
                if hashlib.sha256(content).hexdigest()!=reference["hash"]:
                    raise ValueError("archived report hash mismatch")
                reports.append(reference|{"text":content[:remaining].decode("utf-8",errors="ignore"),"truncated":len(content)>remaining})
                remaining=max(0,remaining-len(content))
            record["reports"]=reports
        return record | {"applicability": application}

    def validate_fact(self, item):
        content = item["content"]
        if item["kind"] != "fact":
            return
        if not item["evidence"]:
            raise ValueError("fact lacks original evidence")
        ref = item["evidence"][0]
        source, _ = self.store.source(ref["source_id"])
        chunk = next(c for c in source["chunks"] if c["ref"] == ref["anchor_ref"])
        if content.get("quote") and content["quote"] not in chunk["text"]:
            raise ValueError("fact quote missing in original anchor")
        if "value" in content:
            if not content.get("raw_value") or not source_number_present(content["raw_value"], chunk["text"]) or numeric(content["raw_value"]) != content["value"]:
                raise ValueError("financial fact numeric/source mismatch")

    def review(self, args, task, role):
        self.check(role)
        if role != "verification":
            raise PermissionError("research agent cannot approve its own proposal")
        proposal = self.store.proposal(args["proposal_id"])
        if args["passed"]:
            self.validate_fact(proposal["item"])
            if proposal["item"]["kind"]=="thesis":
                try:
                    prior=self.store.read(proposal["entry_id"])
                except ValueError:
                    prior=None
                old_scope=prior["item"]["content"].get("metric_scope") if prior else None
                new_scope=proposal["item"]["content"].get("metric_scope")
                if old_scope and old_scope!=new_scope and proposal["item"]["judgment"] in {"supported","refuted"}:
                    raise ValueError("different metric scope cannot directly confirm/refute the prior thesis; reconcile scope first")
        self.store.review(args, task, self.session)
        return {"reviewed": args["proposal_id"], "passed": args["passed"]}
