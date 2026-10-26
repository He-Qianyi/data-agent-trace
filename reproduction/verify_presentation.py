"""Check guide links and exact excerpts against existing public research records."""
import argparse
import json
from pathlib import Path
import re
from urllib.parse import unquote,urlsplit
from .common import ROOT,read,digest


def verify(root=ROOT):
    root=Path(root).resolve()
    guide=(root/'docs/case-study.md').read_text(encoding='utf-8')
    bindings=read(root/'docs/case-study-sources.json')['bindings']
    for b in bindings:
        path=(root/b['path']).resolve()
        if not path.is_relative_to(root) or digest(path)!=b['sha256']:
            raise ValueError('Guide source hash mismatch: '+b['label'])
        value=read(path)
        for token in b['json_pointer'].strip('/').split('/') if b['json_pointer'] else []:
            token=token.replace('~1','/').replace('~0','~')
            value=value[int(token)] if isinstance(value,list) else value[token]
        text=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)
        if b['excerpt'] not in text or b['excerpt'] not in guide:
            raise ValueError('Guide excerpt mismatch: '+b['label'])
    count=0
    for doc in [root/'README.md',*sorted((root/'docs').glob('*.md'))]:
        if doc.name.startswith('._'):continue
        text=doc.read_text(encoding='utf-8')
        for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)',text):
            target=target.strip().split(' ',1)[0].strip('<>')
            parsed=urlsplit(target)
            if parsed.scheme or not parsed.path:continue
            path=(doc.parent/unquote(parsed.path)).resolve()
            if not path.is_relative_to(root) or not path.exists():
                raise ValueError('Broken local guide link: '+str(doc.relative_to(root))+' -> '+target)
            count+=1
    return {'status':'passed','existing_source_excerpts_checked':len(bindings),
            'local_document_links_checked':count,'new_model_calls':0,'research_outputs_modified':False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=ROOT)
    args=p.parse_args();print(json.dumps(verify(args.root),ensure_ascii=False))

if __name__=='__main__':main()
