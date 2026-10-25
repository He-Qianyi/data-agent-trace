"""Prepare frozen views, call task organizer, then call operation annotator."""
import copy
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path

from ..fact_layer.sources import digest, Sources
from . import VERSION
from .contract import REQUIREMENT_PROMPT, OPERATION_PROMPT, REQUIREMENT_SCHEMA, OPERATION_SCHEMA
from .evidence import requirement_input, operation_input, validate_evidence_catalog, read_jsonl, batches, encode
from .provider import Client, ProviderFailure
from .validate import requirements, operations


def write(path, value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+".writing")
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    tmp.replace(path)


def frozen_hashes():
    root=Path(__file__).parent
    values={str(p):digest(p) for p in sorted(root.iterdir()) if p.suffix in (".py",".json",".md")}
    for name in ("trace_processor_v2.py","probe_trace_processor_provider.py"):
        path=root.parents[3]/"scripts"/name
        values[str(path)]=digest(path)
    return values


def prepare(facts, output, model="qwen3.6:35b", max_requests=1200, output_tokens=18000, timeout=600, wire_protocol="responses"):
    facts,output=Path(facts).resolve(),Path(output).resolve()
    manifest=facts/"manifest.jsonl"
    rows=list(read_jsonl(manifest))
    if not rows or any(r["status"] not in ("passed","passed_with_warnings") for r in rows):
        raise ValueError("Fact manifest must contain only validated traces")
    output.mkdir(parents=True,exist_ok=False)
    trace_index=[]; task_keys={}
    for row in rows:
        path=facts/row["path"]
        if digest(path)!=row["sha256"]:
            raise ValueError("Fact trace differs from frozen manifest")
        trace=json.loads(path.read_text())
        if trace["quality"]["source_reference_validation"]!="passed":
            raise ValueError("Fact source validation not passed")
        Sources.from_entries(trace["sources"],verify_hashes=True)
        task=requirement_input(trace)
        key=task["task_prompt_sha256"]
        if key not in task_keys:
            task_keys[key]=len(task_keys)+1
            write(output/"inputs/tasks"/(key+".json"),task)
        view=operation_input(trace)
        validate_evidence_catalog(trace,task["evidence"]+view["evidence"])
        write(output/"inputs/operations"/(row["record_id"]+".json"),view)
        trace_index.append({**{k:row[k] for k in ("record_id","benchmark","agent_id","task_id")},
                            "fact_path":str(path),"fact_sha256":row["sha256"],"task_key":key,
                            "steps":len(trace["steps"]),"units":len(view["units"]),
                            "operation_input_sha256":digest(output/"inputs/operations"/(row["record_id"]+".json"))})
    write(output/"selection.json",trace_index)
    # Copy exact contracts for review, before any network request.
    write(output/"contracts/requirements.schema.json",REQUIREMENT_SCHEMA)
    write(output/"contracts/operations.schema.json",OPERATION_SCHEMA)
    (output/"contracts/requirements.prompt.txt").write_text(REQUIREMENT_PROMPT)
    (output/"contracts/operations.prompt.txt").write_text(OPERATION_PROMPT)
    if wire_protocol not in ("responses","chat"):
        raise ValueError("Unsupported wire protocol")
    config={"schema_version":VERSION,"fact_root":str(facts),"fact_manifest_sha256":digest(manifest),"wire_protocol":wire_protocol,
            "model":model,"provider":"SoCLaaS Responses HTTPS /v1","max_requests":max_requests,"output_tokens":output_tokens,
            "timeout_seconds":timeout,"concurrency":1,"automatic_retries":0,"traces":len(rows),"unique_task_prompts":len(task_keys),
            "implementation_sha256":frozen_hashes(),"selection_sha256":digest(output/"selection.json"),
            "task_input_sha256":{k:digest(output/"inputs/tasks"/(k+".json")) for k in task_keys},
            "input_policy":{"evaluations_sent":False,"post_run_snapshots_sent":False,"final_response_sent":False,
                            "agent_visible_current_assistant_text_sent":True,"entire_model_request_history_sent":False}}
    write(output/"run_config.json",config)
    progress={"status":"prepared","requests_attempted":0,"requirements_completed":0,"traces_completed":0,
              "planned_traces":len(rows),"unique_task_prompts":len(task_keys),"steps":sum(r["steps"] for r in trace_index),
              "units":sum(r["units"] for r in trace_index)}
    write(output/"progress.json",progress)
    return progress


def verify_frozen(root,config):
    if digest(root/"selection.json")!=config["selection_sha256"]:
        raise ValueError("Selection changed after preparation")
    if digest(Path(config["fact_root"])/"manifest.jsonl")!=config["fact_manifest_sha256"]:
        raise ValueError("Fact manifest changed")
    for path,sha in config["implementation_sha256"].items():
        if digest(path)!=sha:
            raise ValueError("Semantic implementation changed after preparation")
    for key,sha in config["task_input_sha256"].items():
        if digest(root/"inputs/tasks"/(key+".json"))!=sha:
            raise ValueError("Task input changed after preparation")


def execute(output, env_file, client_factory=Client):
    root=Path(output).resolve()
    config=json.loads((root/"run_config.json").read_text())
    progress=json.loads((root/"progress.json").read_text())
    if progress["status"] not in ("prepared","paused_by_file"):
        raise ValueError("This attempt is not executable; failed/running/completed attempts are not implicitly retried")
    verify_frozen(root,config)
    rows=json.loads((root/"selection.json").read_text())
    with (root/"job.lock").open("a+") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return _execute(root,config,rows,progress,env_file,client_factory)


def _execute(root,config,rows,progress,env_file,client_factory):
    active={}
    def save(status=None,**extra):
        if status:
            progress["status"]=status
        progress.update(extra,updated_at=datetime.now(timezone.utc).isoformat())
        write(root/"progress.json",progress)
        print(json.dumps(progress,ensure_ascii=False),flush=True)
    def call(prompt,data,schema,label):
        if (root/"pause.requested").exists():
            raise InterruptedError("pause_marker")
        if progress["requests_attempted"]>=config["max_requests"]:
            raise InterruptedError("request_budget_reached")
        progress["requests_attempted"]+=1
        number=progress["requests_attempted"]
        directory=root/"calls"/f"{number:05d}-{label}"
        active.update(call_number=number,call_directory=str(directory.relative_to(root)),stage=label)
        save(active_call=active.copy())
        parsed,receipt=client.ask(prompt,data,schema,directory)
        return parsed,{**receipt,"call_directory":str(directory.relative_to(root))}
    try:
        client=client_factory(config["model"],env_file,config["output_tokens"],config["timeout_seconds"],
                              wire_protocol=config.get("wire_protocol","responses"))
        save("running_requirements")
        task_keys=list(dict.fromkeys(r["task_key"] for r in rows))
        for key in task_keys:
            destination=root/"requirements"/(key+".json")
            if destination.exists():
                continue
            view=json.loads((root/"inputs/tasks"/(key+".json")).read_text())
            data={"evidence":[{k:v for k,v in e.items() if k in ("evidence_id","role","text")} for e in view["evidence"]]}
            parsed,receipt=call(REQUIREMENT_PROMPT,data,REQUIREMENT_SCHEMA,"requirements")
            annotation=requirements(parsed,view)
            write(destination,{"schema_version":VERSION,"task_requirements":annotation,"provider_receipt":receipt})
            progress["requirements_completed"]+=1
            save()
        save("running_operations")
        for row in rows:
            rid=row["record_id"]
            if (root/"traces"/(rid+".json")).exists():
                continue
            if digest(row["fact_path"])!=row["fact_sha256"]:
                raise ValueError("Fact trace changed before annotation")
            view_path=root/"inputs/operations"/(rid+".json")
            if digest(view_path)!=row["operation_input_sha256"]:
                raise ValueError("Operation view changed")
            view=json.loads(view_path.read_text())
            requirement_path=root/"requirements"/(row["task_key"]+".json")
            task_annotation=json.loads(requirement_path.read_text())["task_requirements"]
            # No external metadata: compact accepted requirement descriptions and original-task citations only.
            task_requirements=[{k:r[k] for k in ("requirement_id","kind","description","basis")} for r in task_annotation["requirements"]]
            for r,full in zip(task_requirements,task_annotation["requirements"]):
                r["task_evidence_quotes"]=[c["quote"] for c in full["evidence"]]
            all_units=[]; receipts=[]
            packets=list(batches(view,task_requirements))
            for i,packet in enumerate(packets,1):
                destination=root/"operations"/rid/f"{i:04d}.json"
                if destination.exists():
                    accepted=json.loads(destination.read_text())
                else:
                    parsed,receipt=call(OPERATION_PROMPT,packet,OPERATION_SCHEMA,"operations")
                    accepted={"annotation":operations(parsed,packet,view["evidence"]),"provider_receipt":receipt,
                              "packet_sha256":digest_payload(packet)}
                    write(destination,accepted)
                if accepted["packet_sha256"]!=digest_payload(packet):
                    raise ValueError("Completed semantic packet no longer matches frozen input")
                all_units.extend(accepted["annotation"]["units"]);receipts.append(accepted["provider_receipt"])
                save(active_trace=rid,active_packet=i,trace_packets=len(packets))
            trace=json.loads(Path(row["fact_path"]).read_text())
            annotated=assemble(trace,row,all_units,task_annotation,receipts,requirement_path,root)
            write(root/"traces"/(rid+".json"),annotated)
            progress["traces_completed"]+=1
            save()
        verify_frozen(root,config)
        save("completed",active_call=None)
        write(root/"summary.json",progress)
        return progress
    except ProviderFailure as exc:
        save("stopped_provider_failure",failure=exc.metadata,active_call=active.copy())
    except InterruptedError as exc:
        save("paused_by_file" if str(exc)=="pause_marker" else "stopped_request_budget",reason=str(exc),active_call=active.copy())
    except (ValueError,OSError,KeyError,RuntimeError) as exc:
        # Validator messages contain stable identifiers, not model bodies or credential material.
        safe=str(exc)[:180] if isinstance(exc,ValueError) else type(exc).__name__
        save("stopped_validation_or_runtime_failure",failure={"error_type":type(exc).__name__,"reason":safe},active_call=active.copy())
    write(root/"summary.json",progress)
    return progress


def digest_payload(packet):
    from .evidence import text_hash
    return text_hash(encode(packet))


def assemble(trace,row,units,task_annotation,receipts,requirement_path,root):
    if len({u["unit_id"] for u in units})!=row["units"] or len(units)!=row["units"]:
        raise ValueError("Final semantic unit coverage mismatch")
    by_step={s["step_id"]:[] for s in trace["steps"]}
    for unit in units:
        by_step[unit["step_id"]].append(unit)
    if any(not parts for parts in by_step.values()):
        raise ValueError("Final semantic step coverage mismatch")
    annotated=copy.deepcopy(trace)
    annotated["fact_schema_version"]=annotated["schema_version"]
    annotated["schema_version"]="data-agent-normalized-trace-with-semantics-v1"
    annotated["task"]["requirements_annotation"]=task_annotation
    for step in annotated["steps"]:
        step["annotations"]={"status":"llm_annotated_provenance_validated","parts":by_step[step["step_id"]],
                             "semantic_correctness_certified":False}
    annotated["quality"]["semantic_annotation_run"]=True
    annotated["quality"]["semantic_layer_validation"]={"schema":"passed","literal_quotes":"passed","unit_and_step_coverage":"passed",
                                                        "semantic_correctness_certified":False,"external_llm_judge_used":False}
    annotated["semantic_layer"]={"version":VERSION,"fact_trace_path":row["fact_path"],"fact_trace_sha256":row["fact_sha256"],
                                  "requirements_path":str(requirement_path.relative_to(root)),"requirements_sha256":digest(requirement_path),
                                  "operation_receipts":receipts,"evaluation_provided_to_annotator":False,
                                  "post_run_snapshots_provided_to_annotator":False,"error_diagnosis_run":False}
    return annotated
