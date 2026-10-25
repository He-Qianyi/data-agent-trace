"""Compare terminal candidate runs with recorded v1 controls, without model calls."""
import copy
import importlib.util
from pathlib import Path
import zipfile

spec = importlib.util.spec_from_file_location('audit_local', Path(__file__).with_name('audit.py'))
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
r = a.r


def read_efficiency(initial, logs):
    seen = {}
    a.add_pages(seen, initial)
    rows = []
    for x in logs:
        count = len(list(a.pages(x['result'])))
        novel = a.add_pages(seen, x['result'])
        rows.append({'round': x['round'], 'ordinal': x['ordinal'], 'name': x['name'],
                     'arguments': x['arguments'], 'source_pages': count,
                     'new_source_characters': novel,
                     'fully_repeated_source_read': bool(count and novel == 0)})
    return {'tool_calls': len(logs),
            'fully_repeated_source_reads': sum(x['fully_repeated_source_read'] for x in rows),
            'new_source_characters': sum(x['new_source_characters'] for x in rows),
            'details': rows}


def request_field_audit():
    index = r.b.EvidenceIndex(r.b.BUNDLE)
    records = []
    for rid in r.IDS:
        cache, rows = {}, []
        folder = r.ROOT / 'results' / rid
        for path in sorted((folder / 'tool_reads').glob('*.json')):
            entry = r.b.read(path)
            args, result = entry.get('arguments') or {}, entry['result']
            if (entry['name'] != 'read_exchange' or args.get('surface') != 'request'
                    or result.get('status') != 'source_excerpt'):
                continue
            xid = args['exchange_id']
            if xid not in cache:
                exchange = next(x for x in index.traces[rid]['facts']['model_exchanges'] if x['exchange_id'] == xid)
                raw = index.registry(rid).resolve(exchange['request_ref'])
                if not isinstance(raw, dict):
                    continue
                offset, fields = 1, []
                for key, value in raw.items():
                    start = offset + len(r.b.encode(key)) + 1
                    end = start + len(r.b.encode(value))
                    fields.append({'field': key, 'start': start, 'end': end})
                    offset = end + 1
                assert offset == len(r.b.encode(raw))
                cache[xid] = fields
            start, end = result['block_char_start'], result['block_char_end']
            overlap = [{'field': f['field'], 'characters': max(0, min(end, f['end']) - max(start, f['start']))}
                       for f in cache[xid]]
            rows.append({'round': entry['round'], 'exchange_id': xid, 'start': start, 'end': end,
                         'returned_characters': end - start,
                         'top_level_field_overlap': [x for x in overlap if x['characters']]})
        records.append({'record_id': rid, 'request_field_ranges': cache, 'reads': rows,
                        'returned_request_characters': sum(x['returned_characters'] for x in rows),
                        'tool_definition_characters': sum(f['characters'] for x in rows
                                                          for f in x['top_level_field_overlap'] if f['field'] == 'tools')})
    r.b.save(r.ROOT / 'request_field_read_audit.json', {'records': records,
        'interpretation': 'Field/character overlap only. Tool definitions can matter for tool-use diagnosis, but reading a new source range alone does not establish diagnostic relevance.'})
    return records


def package():
    root = r.ROOT
    plan = r.verify()
    summary = r.b.read(root / 'summary.json')
    audit = r.b.read(root / 'message_and_read_audit.json')
    comparison = []
    for job in plan['jobs']:
        rid = job['record_id']
        control = r.BASE / job['comparison_source'] / 'results' / rid
        old_result = r.b.read(control / 'result.json')
        logs = []
        if rid == 'rawio-bcb49010f78ccd4af446':
            logs += [r.b.read(p) for p in sorted((r.OLD / 'results' / rid / 'tool_reads').glob('*.json'))]
            assert len(logs) == 26
        logs += [r.b.read(p) for p in sorted((control / 'tool_reads').glob('*.json'))]
        assert len(logs) == old_result['tool_calls']
        initial = r.b.read(root / 'inputs' / (rid + '.json'))
        old_efficiency = read_efficiency(initial, logs)
        new_logs = [r.b.read(p) for p in sorted((root / 'results' / rid / 'tool_reads').glob('*.json'))]
        new_efficiency = read_efficiency(initial, new_logs)
        row = next(x for x in summary['traces'] if x['record_id'] == rid)
        comparison.append({'record_id': rid, 'task_id': job['task_id'],
                           'baseline': {'status': old_result['status'], 'model_rounds': old_result['model_rounds'],
                                        'has_final_json': False, **old_efficiency},
                           'candidate': {'status': row['status'], 'model_rounds': row['model_rounds'],
                                         'has_final_json': row['has_final_json'], 'accepted': row['accepted'],
                                         **new_efficiency}})
    r.b.save(root / 'comparison.json', {'records': comparison,
        'duplicate_definition': 'A source-reading tool call whose returned evidence-ID/character intervals add zero characters beyond initial input and earlier returned pages; excludes searches with no source pages.',
        'design': 'Two combined prompt changes versus v1, known non-submitting cases only; not single-factor attribution or held-out generalization.'})
    previous = r.b.read(r.BASE / 'outputs/diagnosis_v1_service_rerun_two_20261018/latest_eight_cases.json')
    latest = {x['record_id']: copy.deepcopy(x) for x in previous['traces']}
    for row in summary['traces']:
        latest[row['record_id']] = {k: row.get(k) for k in
                                    ['record_id', 'task_id', 'agent_id', 'status', 'accepted', 'has_final_json']}
        latest[row['record_id']]['result_source'] = str(root.relative_to(r.BASE))
    ledger = {'design': 'Latest recorded result per case, mixed runs; not eight independent fresh candidate runs.',
              'total': len(latest), 'accepted': sum(bool(x['accepted']) for x in latest.values()),
              'has_final_json': sum(bool(x['has_final_json']) for x in latest.values()),
              'traces': list(latest.values())}
    r.b.save(root / 'latest_eight_cases.json', ledger)
    fields = request_field_audit()
    text = (root / 'report.txt').read_text()
    text += '\n与原始v1的逐条对照：\n\n|轨迹|v1提交JSON|新版提交JSON|v1模型轮数|新版模型轮数|v1完全重复读取|新版完全重复读取|\n|---|---|---|---:|---:|---:|---:|\n'
    for row in comparison:
        old, new = row['baseline'], row['candidate']
        text += f"|{row['task_id']}|{old['has_final_json']}|{new['has_final_json']}|{old['model_rounds']}|{new['model_rounds']}|{old['fully_repeated_source_reads']}|{new['fully_repeated_source_reads']}|\n"
    text += '\n重复读取按已返回的证据ID及字符区间统计，不等同于所有语义相似信息；首次输入中的原文也计入已读取范围。\n'
    text += '消息衔接核查：逐轮请求保留此前模型响应与匹配的工具返回，模型参数与计划一致。\n'
    for row in fields:
        if row['returned_request_characters']:
            text += (f"{row['record_id']}：读取原始请求{row['returned_request_characters']}字符，"
                     f"其中{row['tool_definition_characters']}字符位于工具定义字段。读取新字符不等于获得新的任务诊断依据。\n")
    text += f"当前八条各取最新记录：{ledger['accepted']}/8通过输出合同；只有本轮这两条使用新prompt，不能将此数视为新版在八条上的验证成绩。\n"
    text += '诊断语义与无法定位说明的本地原文核查见semantic_review.json；格式验收不认证根因正确。\n'
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
    print(r.b.encode({'verified_files': len(manifest), 'zip_verified': True,
                      'accepted_this_run': summary['accepted'], 'latest_eight_accepted': ledger['accepted'],
                      'comparison': [{k: v for k, v in row.items() if k in ['task_id']} | {
                          'baseline_repeated': row['baseline']['fully_repeated_source_reads'],
                          'candidate_repeated': row['candidate']['fully_repeated_source_reads']}
                          for row in comparison]}))


if __name__ == '__main__':
    package()
