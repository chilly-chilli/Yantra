"""One durable CNY ledger and wall-clock deadline for every research task."""
from __future__ import annotations

from datetime import datetime, timezone
import threading
import time
from uuid import uuid4

from .schemas import Pricing

MAX_RUN_SECONDS = 7200


class BudgetExceeded(RuntimeError):
    pass


class RunBudget:
    def __init__(self, state, save, clock=time.time):
        self.state, self.save, self.clock = state, save, clock
        self.lock = threading.RLock()

    def remaining(self, closing=False):
        deadline = datetime.fromisoformat(self.state["deadline"]).timestamp()
        reserve = 0 if closing else self.state["limits"]["close_seconds"]
        return max(0, deadline - self.clock() - reserve)

    def check(self):
        if "模型用量超过保守预留，停止进一步模型调用" in self.state["stop_reasons"]:
            raise BudgetExceeded("模型用量超过配置的费用预留上界，请核对计费配置")
        if self.remaining() <= 0:
            raise BudgetExceeded("研究墙钟截止时点已到，预留收尾时间")
        if (self.state_path / "cancel.request").exists():
            raise BudgetExceeded("用户取消")

    def reserve(self, payload, instruction, output_limit, pricing=None, extra_input_tokens=0):
        with self.lock:
            self.check()
            pricing = Pricing.model_validate(pricing or self.state["pricing"])
            # Each UTF-8 byte is a conservative token bound, plus the ADK wrapper.
            input_bound = len((payload + instruction).encode("utf-8")) + 4096 + extra_input_tokens
            amount = (input_bound * pricing.input_per_million + output_limit * pricing.output_per_million) * pricing.cny_per_currency / 1_000_000
            ledger = self.state["model_ledger"]
            committed = sum(x.get("cost_cny", x["reserved_cny"]) for x in ledger)
            service_cost = self.state["budget"].get("external_service_cost_cny", 0) if self.state.get("research_flow_version", 0) >= 2 else 0
            if committed + service_cost + amount >= self.state["limits"]["max_cost_cny"]:
                raise BudgetExceeded("共享人民币模型预算不足")
            receipt = {"id": uuid4().hex, "reserved_cny": amount, "input_token_bound": input_bound,
                       "output_token_bound": output_limit, "status": "reserved", "pricing": pricing.model_dump()}
            ledger.append(receipt)
            self.state["budget"]["model_cost_cny"] = committed + amount
            self.save()
            return receipt

    def reserve_fixed(self, amount, method, basis):
        with self.lock:
            self.check()
            committed = sum(x.get("cost_cny", x["reserved_cny"]) for x in self.state["model_ledger"])
            service_cost = self.state["budget"].get("external_service_cost_cny", 0) if self.state.get("research_flow_version", 0) >= 2 else 0
            if amount < 0 or committed+service_cost+amount >= self.state["limits"]["max_cost_cny"]:
                raise BudgetExceeded("声明的扩展模型费用上界超出剩余预算")
            receipt={"id":uuid4().hex,"reserved_cny":amount,"status":"reserved","method":method,
                     "basis":basis,"actual_bill_available":False}
            self.state["model_ledger"].append(receipt)
            self.state["budget"]["model_cost_cny"]=committed+amount
            self.save()
            return receipt

    def settle(self, receipt, usage):
        with self.lock:
            if usage and usage.get("input_tokens") is not None and usage.get("output_tokens") is not None:
                pricing = Pricing.model_validate(receipt["pricing"])
                cost = (usage["input_tokens"] * pricing.input_per_million + usage["output_tokens"] * pricing.output_per_million) * pricing.cny_per_currency / 1_000_000
                receipt.update(usage=usage, cost_cny=cost, status="usage_accounted", actual_bill_available=False)
                if cost > receipt["reserved_cny"]:
                    self.state["stop_reasons"].append("模型用量超过保守预留，停止进一步模型调用")
            else:
                receipt["status"] = "usage_missing_reservation_retained"
            self.state["budget"]["model_cost_cny"] = sum(x.get("cost_cny", x["reserved_cny"]) for x in self.state["model_ledger"])
            self.save()


def deadline(started, seconds):
    if seconds > MAX_RUN_SECONDS:
        raise ValueError("research run_seconds must not exceed 7200")
    return datetime.fromtimestamp(started + seconds, timezone.utc).isoformat()
