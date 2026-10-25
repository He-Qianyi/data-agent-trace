"""Re-evaluate recorded outputs with the current acceptance policy; no model calls."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from pipeline.stages.diagnosis.validation import POLICY_ID, UPSTREAM_URL, validate_diagnosis_text

EXPERIMENTS = {
    "baseline": "diagnosis_eight_native_tools_20261018",
    "round1": "diagnosis_termination_control_v1_20261018",
    "round2": "diagnosis_confirmation_gate_v1_20261018",
}


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def reference_audit(data, trace, directory, fallback):
    ranges = read(directory / "read_ranges.json") if (directory / "read_ranges.json").exists() else {}
    extra_path = directory / "registered_extra_sources.json"
    if not extra_path.exists():
        extra_path = fallback / "registered_extra_sources.json"
    extras = read(extra_path) if extra_path.exists() else []
    known = {e["evidence_id"] for e in trace["evidence_catalog"]} | {e["evidence_id"] for e in extras} | {"FINAL0001"}
    steps = {s["step_id"] for s in trace["facts"]["steps"]}
    citations, errors = [], []
    for section in ("root_cause_chain", "critical_decision_points"):
        for i, point in enumerate(data.get(section, [])):
            if not isinstance(point, dict):
                continue
            for sid in point.get("step_ids", [point.get("step_id")]):
                if sid is not None and sid not in steps:
                    errors.append({"kind": "unknown_step_id", "section": section, "index": i, "step_id": sid})
            for eid in point.get("evidence_ids", []):
                item = {"section": section, "index": i, "evidence_id": eid,
                        "exists": eid in known, "actually_read": bool(ranges.get(eid))}
                citations.append(item)
                if not item["exists"] or not item["actually_read"]:
                    errors.append({"kind": "reference", **item})
    return {"errors": errors, "citation_checks": citations,
            "affects_acceptance": False, "semantic_entailment_verified": False}


def run(output):
    output = output.resolve()
    assert not output.exists(), "Use a new output directory; recorded experiments are immutable"
    config_path = BASE / "configs/05_diagnosis/CURRENT_ACCEPTANCE.json"
    policy = read(config_path)
    assert policy["policy_id"] == POLICY_ID
    output.mkdir(parents=True)
    before_hashes = {}
    records = []
    baseline = BASE / "outputs" / EXPERIMENTS["baseline"]
    for label, name in EXPERIMENTS.items():
        root = BASE / "outputs" / name
        plan = read(root / "plan.json")
        # Preserve all recorded source artifacts and their previous assessments.
        for path in root.rglob("*"):
            if path.is_file():
                before_hashes[str(path.relative_to(BASE))] = sha(path)
        for job in plan["jobs"]:
            rid = job["record_id"]
            directory = root / "results" / rid
            result = read(directory / "result.json")
            trace = read(BASE / "outputs/stage_d_ten_20261018/traces" / (rid + ".json"))
            steps = {s["step_id"] for s in trace["facts"]["steps"]}
            native = directory / "native_final.txt"
            text = native.read_text() if native.exists() else ""
            acceptance = validate_diagnosis_text(text, steps)
            parsed = None
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError):
                pass
            citations = reference_audit(parsed, trace, directory, baseline / "results" / rid) if isinstance(parsed, dict) else None
            old_path = directory / "mechanical_validation.json"
            old = read(old_path) if old_path.exists() else None
            if isinstance(parsed, dict):
                outcome = "accepted" if acceptance["passed"] else "rejected"
            elif result.get("status") in ("recovery_failed", "transport_censored_before_intervention") or result.get("reason") == "provider_failure_no_retry":
                outcome = "not_assessable_service"
            else:
                outcome = "no_valid_final_json"
            row = {"experiment": label, "record_id": rid, "task_id": job["task_id"],
                   "original_status": result["status"], "outcome": outcome,
                   "has_final_json": isinstance(parsed, dict),
                   "old_strict_passed": bool(old and old["passed"]),
                   "acceptance": acceptance, "independent_reference_audit": citations,
                   "extra_top_level_fields_ignored": sorted(set(parsed) - {"root_cause_chain", "critical_decision_points", "correct_strategy", "stage_labels", "summary"}) if isinstance(parsed, dict) else [],
                   "native_output_sha256": sha(native) if native.exists() else None,
                   "native_output_source": str(native.relative_to(BASE)) if native.exists() else None}
            records.append(row)
            save(output / label / (rid + ".json"), row)
    cohorts = {}
    for label in EXPERIMENTS:
        rows = [r for r in records if r["experiment"] == label]
        cohorts[label] = {"total": len(rows), "accepted": sum(r["acceptance"]["passed"] for r in rows),
                          "old_strict_passed": sum(r["old_strict_passed"] for r in rows),
                          "valid_final_json": sum(r["has_final_json"] for r in rows),
                          "service_censored": sum(r["outcome"] == "not_assessable_service" for r in rows),
                          "non_service_no_valid_json": sum(r["outcome"] == "no_valid_final_json" for r in rows),
                          "reference_errors": sum(len((r["independent_reference_audit"] or {}).get("errors", [])) for r in rows)}
    fixed_ids = {r["record_id"] for r in records if r["experiment"] == "round1" and r["has_final_json"]}
    fixed = {label: {"total": len(fixed_ids), "accepted": sum(r["acceptance"]["passed"] for r in records if r["experiment"] == label and r["record_id"] in fixed_ids)} for label in ("round1", "round2")}
    paired_ids = fixed_ids & {r["record_id"] for r in records if r["experiment"] == "round2" and r["has_final_json"]}
    paired = {label: {"total": len(paired_ids), "accepted": sum(r["acceptance"]["passed"] for r in records if r["experiment"] == label and r["record_id"] in paired_ids)} for label in ("round1", "round2")}
    frozen = read(BASE / "outputs/trace_processor_frozen_v1_20261018/frozen_manifest.json")["files"]
    assert all(sha(BASE / p) == h for p, h in frozen.items()), "Frozen processor changed"
    assert all(sha(BASE / p) == h for p, h in before_hashes.items()), "Recorded experiment changed"
    summary = {"acceptance_standard": POLICY_ID, "upstream": UPSTREAM_URL,
               "cohorts": cohorts, "fixed_six_controls": fixed, "both_final_outputs": paired,
               "new_model_requests": 0, "output_repairs": 0, "prompts_changed": False,
               "original_experiment_files_unchanged": len(before_hashes),
               "frozen_processor_files_unchanged": len(frozen),
               "acceptance_certifies_semantics": False}
    save(output / "summary.json", summary)
    save(output / "records.json", records)
    save(output / "preserved_input_hashes.json", before_hashes)
    save(output / "acceptance_policy.json", policy)
    (output / "method").mkdir()
    for source in (Path(__file__), BASE / "pipeline/stages/diagnosis/validation.py", BASE / "tests/test_diagnosis_acceptance.py"):
        shutil.copy2(source, output / "method" / source.name)
    shutil.copy2(BASE / "work/codetracer_diagnosis_comparison_20261018/src/codetracer/services/validation.py", output / "method/upstream_validation.py")
    lines = ["诊断验收改为CodeTracer detailed兼容标准", "",
             "验收检查：可解析JSON对象、五个核心字段存在、已提供且非null的关键决策step_id属于轨迹。额外字段原样保留并忽略。",
             "与CodeTracer一样，不把字段存在性验收当成语义认证。引用核查独立报告，不影响本口径的机械验收。",
             "本次只重验已有结果；LLM请求0、输出修复0、prompt改动0。历史严格验收报告保持不变。", "",
             "|结果组|旧严格验收|新验收|", "|---|---:|---:|"]
    for label, title in (("baseline", "原始v1对照"), ("round1", "第一轮终止控制"), ("round2", "第二轮确认门槛")):
        row = cohorts[label]
        lines.append(f"|{title}|{row['old_strict_passed']}/8|{row['accepted']}/8|")
    lines += ["", f"固定六条第一轮已完成样本：第一轮{fixed['round1']['accepted']}/6，第二轮{fixed['round2']['accepted']}/6。",
              f"五条两轮都有最终JSON的样本：两轮均为{paired['round1']['accepted']}/5；原来的额外字段失败不再计为诊断失败。", "",
              "第二轮agriculture_16仍不通过：第12轮返回文本取证标记而不是诊断JSON，HTTP200/stop；不是连接中断或token截断。",
              "两条历史连接中断本次未重跑，也不因更换标准自动变成通过。第二轮此前已经完成这两条，不计作prompt语义改善。",
              "语义复核结论不变：civic的因果越界、stock的SQL机制混淆仍需处理。通过机械验收不表示这些诊断正确。", "",
              "后续使用configs/05_diagnosis/CURRENT_ACCEPTANCE.json及pipeline.stages.diagnosis.validation。历史冻结实验runner仍保留原严格校验以便复现，不再用其旧通过数比较当前验收效果。",
              f"原实验文件{len(before_hashes)}个与冻结Processor文件{len(frozen)}个均验证未改动。",
              "上游版本：" + UPSTREAM_URL]
    (output / "report.txt").write_text("\n".join(lines) + "\n")
    manifest = {str(p.relative_to(output)): sha(p) for p in sorted(output.rglob("*")) if p.is_file()}
    save(output / "manifest.json", {"files": manifest})
    with zipfile.ZipFile(output / "bundle.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for rel in manifest:
            z.write(output / rel, rel)
        z.write(output / "manifest.json", "manifest.json")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output)
