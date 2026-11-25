"""Read sealed viewing records and compare future behavior against a measured CAT anchor."""
import hashlib
import json
import math
from pathlib import Path

from scipy.special import expit

from .model import Batch, EVENT, Prior, fit, initial, parameters, score


def load(path):
    with Path(path).open(encoding="utf-8-sig") as stream:
        return json.load(stream)


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value)


def whole(value):
    return type(value) is int and value >= 0


def anchor(record, digest, field):
    if not isinstance(record, dict) or record.get("version") != 1 or record.get("model") != "3PL word CAT":
        raise ValueError("需要单词能力试测导出的完整测量记录")
    params = record.get("parameters", {})
    desc = record.get("fits", {}).get(field, {})
    if params.get("tableSha256") != digest:
        raise ValueError("测量记录与难度表的指纹不匹配")
    theta, se, slope = desc.get("theta"), desc.get("se"), params.get("discrimination")
    if (not all(map(numeric, (theta, se, slope))) or se <= 0 or slope <= 0
            or params.get("thetaByField", {}).get(field) != theta):
        raise ValueError("能力参数或标准误无效")
    return Prior(theta, se, slope)


def parse_session(data, table, digest, field, prior):
    if (not isinstance(data, dict) or data.get("version") != 1 or data.get("kind") != "word-viewing-study"
            or data.get("field") != field or data.get("tableSha256") != digest):
        raise ValueError("观看记录格式、量尺或难度表指纹不匹配")
    if not all(isinstance(data.get(k), str) and data[k].strip() for k in ("clip", "domain", "id")):
        raise ValueError("记录缺少片段、领域或会话代号")
    start, end = data.get("started"), data.get("ended")
    if not whole(start) or not whole(end) or end < start or not isinstance(data.get("exposures"), list):
        raise ValueError("需要已停止且带有效时间的观看记录")
    rows, positions = [], set()
    for idx, r in enumerate(data["exposures"], 1):
        if not isinstance(r, dict) or not all(whole(r.get(k)) for k in ("cue", "start", "end", "at")):
            raise ValueError(f"第 {idx} 条的出现位置或时间无效")
        pos = (r["cue"], r["start"], r["end"])
        if pos in positions or pos[1] >= pos[2] or not start <= r["at"] <= end:
            raise ValueError(f"第 {idx} 条出现重复或超出会话范围")
        positions.add(pos)
        if (type(r.get("shown")) is not bool or r.get("event") not in EVENT or not isinstance(r.get("word"), str)
                or not numeric(r.get("pUnknown")) or not 0 <= r["pUnknown"] <= 1):
            raise ValueError(f"第 {idx} 条的显示状态、反馈或概率无效")
        item = table.get(r["word"])
        if not isinstance(item, dict) or not numeric(item.get(field)):
            raise ValueError(f"第 {idx} 条缺少配套难度")
        b = item[field]
        expected = expit(prior.slope * (b - prior.theta))
        if not math.isclose(expected, r["pUnknown"], abs_tol=1e-9, rel_tol=1e-9):
            raise ValueError(f"第 {idx} 条概率与导入的试测参数不一致")
        rows.append({**r, "b": b})
    return {k: data[k] for k in ("clip", "domain", "id", "started", "ended")} | {"rows": rows}


def report(record_file, table_file, field, train_files, test_files, *, half_life=90,
           domain_bias=True, viewing_discount=True, fit_feedback=True):
    if not numeric(half_life) or half_life <= 0:
        raise ValueError("半衰期必须为正数")
    if field not in ("b_frequency", "b_initial") or not train_files or not test_files:
        raise ValueError("请指定量尺、先前观看记录和随后留出的独立片段")
    raw = Path(table_file).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    table = json.loads(raw.decode("utf-8-sig"))
    if (not isinstance(table, dict) or table.get("version") != 1 or table.get("scale") != "natural-log lemma rank"
            or not isinstance(table.get("words"), dict)):
        raise ValueError("难度表格式无效")
    prior = anchor(load(record_file), digest, field)

    def parse(files):
        out = []
        for path in files:
            try:
                out.append(parse_session(load(path), table["words"], digest, field, prior))
            except (ValueError, TypeError, AttributeError) as e:
                raise ValueError(f"{Path(path).name}：{e}") from e
        return sorted(out, key=lambda s: (s["started"], s["id"]))

    train, test = parse(train_files), parse(test_files)
    ordered = train + test
    if len({s["id"] for s in ordered}) != len(ordered) or len({s["clip"] for s in ordered}) != len(ordered):
        raise ValueError("存在重复会话或同一视频代号；请为留出集使用独立视频")
    if any(left["ended"] > right["started"] for left, right in zip(ordered, ordered[1:])):
        raise ValueError("观看时间重叠或训练数据晚于留出片段")
    domains = sorted({s["domain"] for s in train})
    cutoff = test[0]["started"]
    training = Batch.build(train, domains, reference=cutoff, half_life=half_life)
    if not training.included or not training.weight.sum():
        raise ValueError("训练数据没有可用的单词观看事件")
    x, iterations = fit(training, prior, domain_bias=domain_bias, viewing_discount=viewing_discount, fit_feedback=fit_feedback)
    baseline = initial(prior, domains)
    baseline[1] = 0.  # The original CAT-based policy used no viewing discount.

    def heldout(sessions):
        data = Batch.build(sessions, domains)
        base = score(baseline, data, prior)
        updated = score(x, data, prior)
        return {"included": updated["included"], "excluded": updated["excluded"],
                "anchorEventLogLoss": base["eventLogLoss"], "updatedEventLogLoss": updated["eventLogLoss"]}

    clips = [{"clip": s["clip"], "domain": s["domain"], **heldout([s])} for s in test]
    overall = heldout(test)
    usable = [row for row in clips if row["included"]]
    overall["deltaEventLogLoss"] = (overall["updatedEventLogLoss"] - overall["anchorEventLogLoss"]) if usable else None
    overall["leaveOneClipOutRange"] = None
    if len(usable) > 1:
        # Sensitivity to each complete clip, including differences in exposure count by clip.
        deltas = [(row["included"], row["updatedEventLogLoss"] - row["anchorEventLogLoss"]) for row in usable]
        total = sum(n for n, _ in deltas)
        summed = math.fsum(n * d for n, d in deltas)
        remaining = [(summed - n * d) / (total - n) for n, d in deltas]
        overall["leaveOneClipOutRange"] = [min(remaining), max(remaining)]

    return {"version": 1, "field": field, "tableSha256": digest, "trainingCutoff": cutoff,
            "trainingClips": [s["clip"] for s in train], "halfLifeDays": half_life, "iterations": iterations,
            "options": {"domainBias": domain_bias, "viewingDiscount": viewing_discount, "fitFeedback": fit_feedback},
            "anchor": {"theta": prior.theta, "se": prior.se, "discrimination": prior.slope},
            "estimate": parameters(x, domains), "training": score(x, training, prior),
            "heldOut": overall, "byClip": clips}
