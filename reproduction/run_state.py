"""Portable-run state, exclusive ownership and append-only model-call checkpoints.

Only new portable runs have this journal. Historical experiment directories are
never adopted. Completed responses are reused; uncertain requests are never
resent unless the caller explicitly requests another attempt.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid
from .common import read,digest


EXIT_CODES={'completed':0,'invalid_input':2,'budget_exhausted':3,
            'provider_failed':4,'execution_failed':4,'incomplete_call':4,'output_unusable':5,'paused':6,'busy':7}


class RunStopped(RuntimeError):
    def __init__(self,status,reason):
        self.status,self.reason=status,reason
        super().__init__(reason)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
                                     separators=(',',':')).encode()).hexdigest()


def atomic_save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')
            stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def file_identity(root):
    """Bind actual input bytes, excluding OS metadata; never bind credentials."""
    root=Path(root).resolve()
    return {str(p.relative_to(root)):digest(p) for p in sorted(root.rglob('*'))
            if p.is_file() and not p.name.startswith('._') and '__pycache__' not in p.parts}


def research_identity(root):
    """Bind the actual released method/helper bytes used by a new run."""
    root=Path(root)
    rows=read(root/'docs/research-source-manifest.json')
    return {row['file']:digest(root/row['file']) for row in rows}


def diagnosis_summary(results):
    statuses=[r['status'] for r in results]
    if results and all(s=='mechanically_passed' for s in statuses):status='completed'
    elif any(s in ('round_budget_exhausted','tool_budget_exhausted') for s in statuses):status='budget_exhausted'
    elif 'invalid_input' in statuses:status='invalid_input'
    elif 'busy' in statuses:status='busy'
    elif 'paused' in statuses:status='paused'
    elif 'incomplete_call' in statuses:status='incomplete_call'
    elif 'execution_failed' in statuses:status='execution_failed'
    elif any(s in ('provider_failed','stopped') for s in statuses):status='provider_failed'
    else:status='output_unusable'
    return {'status':status,'exit_code':EXIT_CODES[status],
            'traces_finished':sum(s in ('mechanically_passed','mechanically_failed','invalid_json','output_truncated') for s in statuses),
            'traces_accepted':sum(s=='mechanically_passed' for s in statuses),
            'trace_statuses':statuses,'acceptance_standard':'codetracer-detailed-compatible-v1',
            'semantic_accuracy_measured':False}


class Journal:
    def __init__(self,output,identity,resume=False,retry_incomplete=False):
        self.output=Path(output).resolve();self.identity=identity
        self.resume=resume;self.retry_incomplete=retry_incomplete
        self.reused=self.recovered=self.new_calls=0;self.lock=None
        self.session=uuid.uuid4().hex

    def __enter__(self):
        if self.resume:
            if not (self.output/'.resume/run.json').is_file():
                raise RunStopped('invalid_input','Output is not a resumable portable run; historical directories are not adopted.')
        else:
            try:self.output.mkdir(parents=True,exist_ok=False)
            except FileExistsError as exc:
                raise RunStopped('invalid_input','Output already exists; use --resume only for a matching portable run.') from exc
        cache=self.output/'.resume';cache.mkdir(exist_ok=True)
        self.lock=(cache/'run.lock').open('a+')
        try:fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.lock.close();self.lock=None
            raise RunStopped('busy','Another process owns this run; no request was sent.') from exc
        try:
            path=cache/'run.json'
            if self.resume:
                saved=read(path)
                if saved.get('format')!='portable-run-v1' or saved.get('identity')!=self.identity:
                    raise RunStopped('invalid_input','Inputs, model settings, prompts or implementation changed; use a new output directory.')
            else:atomic_save(path,{'format':'portable-run-v1','identity':self.identity})
            atomic_save(cache/'sessions'/f'{self.session}.json',{'resume':self.resume,
                'retry_incomplete_calls':self.retry_incomplete,'new_request_attempts':0,'reused_model_responses':0})
            return self
        except BaseException:
            self.__exit__(None,None,None);raise

    def __exit__(self,*args):
        if self.lock:
            fcntl.flock(self.lock,fcntl.LOCK_UN);self.lock.close();self.lock=None

    def stats(self):
        result={'new_request_attempts':self.new_calls,'reused_model_responses':self.reused,
                'recovered_saved_responses':self.recovered,'session_id':self.session}
        atomic_save(self.output/'.resume/sessions'/f'{self.session}.json',
                    {**result,'resume':self.resume,'retry_incomplete_calls':self.retry_incomplete})
        return result

    def _verify(self,attempt):
        try:return self._verified_value(attempt)
        except RunStopped:raise
        except (OSError,ValueError,KeyError,TypeError) as exc:
            raise RunStopped('invalid_input','Saved call checkpoint is unreadable; no request was sent.') from exc

    def _verified_value(self,attempt):
        completed=read(attempt/'completed.json')
        for name,sha in completed['files'].items():
            path=(attempt/name).resolve()
            if not path.is_relative_to(attempt.resolve()) or not path.is_file() or digest(path)!=sha:
                raise RunStopped('invalid_input','Saved call checkpoint changed; no request was sent.')
        return read(attempt/'returned.json')

    def _complete(self,attempt,value,recovered=False):
        atomic_save(attempt/'returned.json',value)
        files={str(p.relative_to(attempt)):digest(p) for p in sorted(attempt.rglob('*'))
               if p.is_file() and not p.name.startswith('._') and p.name not in ('completed.json','attempt.json')}
        atomic_save(attempt/'completed.json',{'files':files,'recovered_from_saved_transport':recovered})
        return value

    def _publish(self,attempt,destination):
        destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
        for p in (attempt/'native').glob('*.json'):
            if p.name.startswith('._'):continue
            target=destination/p.name
            if target.exists():
                if digest(target)!=digest(p):
                    raise RunStopped('invalid_input','Published call bytes conflict with the saved checkpoint.')
            else:
                fd,name=tempfile.mkstemp(prefix='.'+target.name+'.',dir=target.parent)
                try:
                    with os.fdopen(fd,'wb') as stream:
                        stream.write(p.read_bytes());stream.flush();os.fsync(stream.fileno())
                    os.replace(name,target)
                finally:
                    if os.path.exists(name):os.unlink(name)

    def call(self,key,request,ask,destination,before_attempt,recover=None):
        entry=self.output/'.resume/calls'/fingerprint(key)
        intent={'key':key,'request_fingerprint':fingerprint(request)}
        if (entry/'intent.json').exists():
            if read(entry/'intent.json')!=intent:
                raise RunStopped('invalid_input','Saved call input changed; use a new output directory.')
        else:atomic_save(entry/'intent.json',intent)
        attempts=sorted(p for p in entry.glob('attempt-*') if p.is_dir())
        if attempts:
            last=attempts[-1]
            if (last/'completed.json').exists():
                value=self._verify(last);self.reused+=1;self.stats();self._publish(last,destination)
                return value
            # A complete native receipt/response can outlive an interrupted checkpoint write.
            value=None
            if recover and all((last/'native'/name).is_file() for name in ('request.json','response.json','receipt.json')):
                try:value=recover(last/'native')
                except Exception:value=None
            if value is not None:
                value=self._complete(last,value,True);self.recovered+=1;self.reused+=1
                self.stats();self._publish(last,destination);return value
            if not self.retry_incomplete:
                raise RunStopped('incomplete_call','A prior request has no complete reusable response. Inspect its attempt directory; --retry-incomplete-calls explicitly permits another request.')
        before_attempt()
        attempt=entry/f'attempt-{len(attempts)+1:04d}';attempt.mkdir()
        atomic_save(attempt/'attempt.json',{'state':'request_started','request_fingerprint':intent['request_fingerprint'],
                    'session_id':self.session,'automatic_retry':False})
        self.new_calls+=1;self.stats()
        try:value=ask(attempt/'native')
        except BaseException as exc:
            atomic_save(attempt/'attempt.json',{'state':'interrupted' if isinstance(exc,(KeyboardInterrupt,SystemExit)) else 'failed',
                'error_type':type(exc).__name__,'request_fingerprint':intent['request_fingerprint'],
                'session_id':self.session,'automatic_retry':False})
            if isinstance(exc,(KeyboardInterrupt,SystemExit,RunStopped)):raise
            reason=getattr(exc,'metadata',{}).get('reason')
            status='output_unusable' if type(exc).__name__ in ('ValidationError','ProviderOutputError') or reason=='provider_annotation_output_unusable' else 'provider_failed'
            raise RunStopped(status,'Model call did not complete ('+type(exc).__name__+'); inspect the retained attempt.') from exc
        value=self._complete(attempt,value);self._publish(attempt,destination)
        return value
