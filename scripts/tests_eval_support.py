"""Controlled fictional text corpus for evaluation; no user materials."""

from yantra.sources import DiscoveryResult


class FixedSources:
    id = "eval_fixed"

    def discover(self, request, cursor=None):
        return DiscoveryResult([], ["synthetic_fixed_corpus_no_network"])

    def fetch(self, candidate, budget):
        raise ValueError("Fixed evaluation corpus cannot fetch remote material.")


def material_text(variant):
    header = "公司：EVAL_SYNTHETIC（完全虚构合成评估）\n披露日期：2026-10-01\n"
    if variant == "missing":
        return header + "经营业务：工业制造。本资料没有任何财务金额、证券价格或市场共识。\n"
    text = header + "币种：CNY，金额单位：million，合并口径；期间：FY2025。\n"
    text += "营业收入：1200\n营业利润：180\n净利润：132\n经营现金流：192\n资本开支：72\n债务：150\n现金：50\n股份数：100 million股\n"
    if variant == "conflict":
        text += "另一份同日合并FY2025披露列示营业收入：999 million CNY。两值冲突，原因未提供。\n"
    return text
