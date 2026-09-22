"""Business defaults and run identity policy; no storage or execution engine."""
from pathlib import Path
DEFAULT={'base_url':'http://127.0.0.1:8000/v1','model':'qwen3.8-27b','max_calls':16,'max_output_tokens':3500,
         'timeout_s':240,'max_cases':4,'identity_docs':12,'identity_images':12,'max_docs':2,'max_chars_per_doc':6500,'max_input_chars':13000,
         'max_images':2,'max_image_bytes':8*1024*1024,'cos_base_url':None}


def check_run_location(run, project, dataset):
    run, project, dataset = Path(run).resolve(), Path(project).resolve(), Path(dataset).resolve()
    from curation.preparation.records import run_relative
    run_relative(run)
    if not run.is_relative_to(project / 'curation') or run.is_relative_to(dataset):
        raise ValueError('run identity must be under project/curation/<pipeline>/runs, outside the lake')
