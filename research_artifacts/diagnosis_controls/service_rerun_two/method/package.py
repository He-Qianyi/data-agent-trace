"""Package terminal results and the mixed-run latest-case ledger; no model calls."""
import importlib.util
from pathlib import Path
import zipfile

spec = importlib.util.spec_from_file_location('rerun', Path(__file__).with_name('run.py'))
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def package():
    root = r.ROOT
    summary = r.b.read(root / 'summary.json')
    audit = r.b.read(root / 'message_and_read_audit.json')
    latest = {x['record_id']: x for x in r.b.read(r.BASE / 'outputs/diagnosis_v1_round24_20261018/records.json')}
    for row in summary['traces']:
        latest[row['record_id']] = {**row, 'result_source': str(root.relative_to(r.BASE))}
    rows = [{k: x.get(k) for k in ['record_id', 'task_id', 'agent_id', 'status', 'accepted', 'has_final_json', 'result_source']}
            for x in latest.values()]
    ledger = {'design': 'latest recorded result per case; not eight fresh independent reruns',
              'total': len(rows), 'accepted': sum(bool(x['accepted']) for x in rows),
              'has_final_json': sum(bool(x['has_final_json']) for x in rows),
              'service_interrupted': sum(x['status'] in ['recovery_failed', 'failed'] and not x['has_final_json'] for x in rows),
              'nonservice_no_final_json': sum(x['status'] == 'round_budget_exhausted' for x in rows),
              'traces': rows}
    r.b.save(root / 'latest_eight_cases.json', ledger)
    text = (root / 'report.txt').read_text()
    text += '\n消息衔接与取证核查（不新增模型调用）：\n'
    for row in audit['records']:
        text += (f"{row['task_id']}：核对请求{row['requests_verified']}次、响应{row['responses_verified']}次，"
                 f"此前模型消息和工具返回均正确带入后续请求；完全重复的原文读取{row['fully_repeated_source_reads']}次。\n")
    text += ('\n当前八条各取最新结果的汇总（不是八条独立重跑）：'
             f"{ledger['accepted']}/8通过输出合同，{ledger['nonservice_no_final_json']}条正常交互但达到轮数上限仍未提交。\n")
    text += '\n语义核查：agriculture_20对输出文件和国家计数的部分观察有原文支持，但任务未固定全球聚合粒度，因此不能将其粒度选择当作已确认根因。具体见semantic_review.json。\n'
    (root / 'report.txt').write_text(text)
    for path in [Path(__file__), Path(__file__).with_name('audit.py')]:
        (root / 'method' / path.name).write_bytes(path.read_bytes())
    manifest = {str(p.relative_to(root)): r.b.sha(p) for p in sorted(root.rglob('*'))
                if p.is_file() and p.name not in ['manifest.json', 'bundle.zip', 'worker.lock']}
    r.b.save(root / 'manifest.json', {'files': manifest})
    with zipfile.ZipFile(root / 'bundle.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        for rel in manifest:
            z.write(root / rel, rel)
        z.write(root / 'manifest.json', 'manifest.json')
    with zipfile.ZipFile(root / 'bundle.zip') as z:
        assert z.testzip() is None
    assert all(r.b.sha(root / rel) == h for rel, h in manifest.items())
    print(r.b.encode({'files_verified': len(manifest), 'zip_verified': True,
                      'latest_eight_accepted': ledger['accepted'], 'latest_eight_no_final_json': ledger['nonservice_no_final_json']}))


if __name__ == '__main__':
    package()
