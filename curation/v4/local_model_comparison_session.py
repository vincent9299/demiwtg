"""Temporarily lend preannotation GPUs to a bounded local model comparison, then restore."""
import argparse, json, os, signal, subprocess, sys, time
from pathlib import Path
import httpx
from .contracts import immutable, run_lock
from .compare_fidelity import MODEL_DIRS

ROOT=Path(__file__).resolve().parents[2]
ANNOT=ROOT/'state/curation/image_preannotation_v1'


def command(pid):
    try:return Path(f'/proc/{pid}/cmdline').read_bytes().decode().strip('\0').split('\0')
    except FileNotFoundError:return []


def matching(fragment):
    return [int(p.name) for p in Path('/proc').iterdir() if p.name.isdigit() and fragment in ' '.join(command(int(p.name)))]


def ready(port,model):
    try:
        with httpx.Client(timeout=3,trust_env=False) as c:
            r=c.get(f'http://127.0.0.1:{port}/v1/models');r.raise_for_status()
            return any(x['id']==model for x in r.json()['data'])
    except (httpx.HTTPError,ValueError,KeyError):return False


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--inputs',type=Path,required=True)
    parser.add_argument('--task',choices=['fidelity','vision','image_filter'],default='fidelity')
    args=parser.parse_args();run=args.run.resolve();run.mkdir(parents=True,exist_ok=True)
    def event(status,**extra):
        data={'time':time.time(),'status':status,**extra}
        with (run/'events.jsonl').open('a') as f:f.write(json.dumps(data,ensure_ascii=False)+'\n')
        print(json.dumps(data,ensure_ascii=False),flush=True)
    def wait_for(fn,seconds,label,process=None):
        deadline=time.monotonic()+seconds
        while not fn():
            if process is not None and process.poll() is not None:raise RuntimeError(f'{label}: server exited {process.returncode}')
            if time.monotonic()>deadline:raise TimeoutError(label)
            time.sleep(5)
        event(label)
    def spawn(cmd,log):
        env=os.environ.copy();env['PATH']=str(Path(sys.executable).parent)+os.pathsep+env.get('PATH','')
        with log.open('ab') as f:return subprocess.Popen(cmd,cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=f,stderr=f,start_new_session=True)
    def stop_owned(process):
        if process and process.poll() is None:
            os.killpg(process.pid,signal.SIGTERM)
            try:process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=30)
    with run_lock(run):
        if (ANNOT/'STOP').exists():raise RuntimeError('Preannotation already paused; do not assume ownership')
        servers=matching('vllm.entrypoints.cli.main serve '+str(ROOT.parent/'models/Qwen3.8-27B'))
        if len(servers)!=1 or not ready(8000,'qwen3.8-27b'):raise RuntimeError('Expected one healthy original Qwen service')
        pid=servers[0];original=command(pid);original_identity=Path(f'/proc/{pid}/stat').read_text().split()[21]
        immutable(run/'resource_before.json',{'server_pid':pid,'server_command':original,'process_start':original_identity,
                  'progress':json.loads((ANNOT/'progress.json').read_text()) if (ANNOT/'progress.json').exists() else None,
                  'authorization':'AGENTS GPU lending clause + user requested four local model comparison'})
        owned=None;stopped_original=False
        try:
            (ANNOT/'STOP').touch();event('preannotation_drain_requested')
            wait_for(lambda:not matching('curation.image_preannotate run --run '+str(ANNOT)),600,'preannotation_drained')
            wait_for(lambda:not matching('curation.image_supervisor --run '+str(ANNOT)),60,'supervisor_paused')
            def evaluate(model,port):
                cmd=[sys.executable,'-m',({'vision':'curation.v4.compare_vision','image_filter':'curation.v4.compare_image_filter','fidelity':'curation.v4.compare_fidelity'}[args.task]),'--inputs',str(args.inputs.resolve()),
                     '--run',str(run/model),'--model',model,'--port',str(port)]
                event('evaluation_started',model=model)
                result=subprocess.run(cmd,cwd=ROOT,timeout=7200)
                event('evaluation_finished',model=model,returncode=result.returncode)
            evaluate('qwen3.8-27b',8000)
            if command(pid)!=original or Path(f'/proc/{pid}/stat').read_text().split()[21]!=original_identity:
                raise RuntimeError('Original process identity changed')
            os.kill(pid,signal.SIGTERM);stopped_original=True
            wait_for(lambda:not command(pid),180,'original_server_stopped')
            for model in ['qwen3.6-35b-a3b','gemma-4-26b-a4b-it','gemma-4-31b-it']:
                model_run=run/model;model_run.mkdir(exist_ok=True)
                cmd=[sys.executable,'-m','vllm.entrypoints.cli.main','serve',str(ROOT.parent/'models'/MODEL_DIRS[model]),
                     '--served-model-name',model,'--host','127.0.0.1','--port','8001',
                     '--tensor-parallel-size','2','--gpu-memory-utilization','0.90','--max-model-len','32768',
                     '--max-num-seqs','2','--enforce-eager']
                cmd += ['--limit-mm-per-prompt',json.dumps({'image':4 if args.task=='image_filter' else 1})] if args.task in {'vision','image_filter'} else ['--language-model-only']
                immutable(model_run/'server_command.json',{'command':cmd})
                try:
                    owned=spawn(cmd,model_run/'server.log');event('model_server_starting',model=model,pid=owned.pid)
                    wait_for(lambda:ready(8001,model),600,'model_server_ready',owned)
                    evaluate(model,8001)
                except Exception as error:event('model_failed',model=model,error=repr(error))
                finally:stop_owned(owned);owned=None
        finally:
            stop_owned(owned)
            if stopped_original:
                event('restoring_original_service')
                restored=spawn(original,run/'restore_server.log')
                wait_for(lambda:ready(8000,'qwen3.8-27b'),600,'original_service_restored',restored)
            if not ready(8000,'qwen3.8-27b'):raise RuntimeError('Original service not healthy; STOP retained')
            wait_for(lambda:not matching('bash '+str(ROOT/'curation/run_image_pipeline.sh')),60,'old_launcher_exited')
            (ANNOT/'STOP').unlink(missing_ok=True)
            launcher=spawn(['bash',str(ROOT/'curation/run_image_pipeline.sh')],run/'restore_launcher.log')
            event('preannotation_resume_started',launcher_pid=launcher.pid)
            wait_for(lambda:bool(matching('curation.image_preannotate run --run '+str(ANNOT))),120,'preannotation_resumed')
            event('finished_resources_restored')


if __name__=='__main__':main()
