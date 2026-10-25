import argparse
import json
from pathlib import Path

from .run import prepare,execute


def main(argv=None):
    p=argparse.ArgumentParser(description="Stage A task requirements, then Stage B operation semantics; no automatic retries")
    p.add_argument("action",choices=("prepare","execute"))
    p.add_argument("--output",required=True)
    p.add_argument("--facts",default="outputs/fact_layer_v1_20261017")
    p.add_argument("--model",default="qwen3.6:35b")
    p.add_argument("--env-file",default=str(Path(__file__).resolve().parents[4]/".env.soclaas"))
    p.add_argument("--max-requests",type=int,default=1200)
    p.add_argument("--output-tokens",type=int,default=18000)
    p.add_argument("--timeout",type=int,default=600)
    p.add_argument("--wire-protocol",choices=("responses","chat"),default="responses")
    a=p.parse_args(argv)
    if not 1<=a.max_requests<=5000 or not 1000<=a.output_tokens<=25000 or not 1<=a.timeout<=1800:
        p.error("Invalid request/token/timeout budget")
    if a.action=="prepare":
        result=prepare(a.facts,a.output,a.model,a.max_requests,a.output_tokens,a.timeout,a.wire_protocol)
    else:
        result=execute(a.output,a.env_file)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result["status"] in ("prepared","completed") else 2


if __name__=="__main__":
    raise SystemExit(main())
