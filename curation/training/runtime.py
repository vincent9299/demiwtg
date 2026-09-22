"""CLI loads the canonical notebook graph; no second orchestration implementation."""
import argparse
import json
from pathlib import Path

from curation.preparation.records import ROOT, digest, file_record, read
from project import resolve_root

DEFAULT_MODEL = {"base_url": "http://127.0.0.1:8000/v1", "model": "qwen3.8-27b",
                 "max_output_tokens": 4096, "max_calls": 12, "timeout_s": 240}


def notebook_path(branch="training"):
    if branch != "training":
        raise ValueError("Use the requested pipeline package")
    return Path(__file__).with_name("debug.ipynb")


def load_pipeline(branch="training"):
    path = notebook_path(branch)
    cells = read(path)["cells"]
    scope = {"__name__": "v2_training_notebook"}
    for cell in cells:
        if cell["cell_type"] == "code" and "pipeline" in cell.get("metadata", {}).get("tags", []):
            exec(compile("".join(cell["source"]), str(path), "exec"), scope)
    return scope["run_pipeline"]


def graph_version(branch="training"):
    return digest(["".join(c["source"]) for c in read(notebook_path(branch))["cells"]
                   if "pipeline" in c.get("metadata", {}).get("tags", [])])


def config(mode, registry=None, max_calls=12, author_model=None, author_effort=None,
           *, seed=0, max_units=2, tasks_per_unit=1, task_types=('t2i', 'edit'),
           max_context_chars=60000, max_reference_images=16, max_source_images=3, scene_search=None,
           concepts=None, target_candidates_per_task=1, training_design="target_aware", design_target_candidates=4,
           training_sample_goal=1, reference_batch_size=4, max_target_cycles=2, max_training_attempts=100):
    if mode == "local" and author_model not in {None, DEFAULT_MODEL["model"]}:
        raise ValueError("Local transport uses the configured local Qwen, not an offline author")
    if max_units is not None and max_units < 1 or tasks_per_unit < 1 or max_calls < 1:
        raise ValueError('Run budgets must be positive')
    if not task_types or set(task_types) - {'t2i', 'edit'} or max_context_chars < 100 or max_reference_images < 1 or max_source_images < 0:
        raise ValueError('Invalid task types or context limits')
    if type(target_candidates_per_task) is not int or target_candidates_per_task < 1:
        raise ValueError('Target candidate budget must be a positive integer')
    if concepts is not None and (not concepts or isinstance(concepts, str) or
            any(not isinstance(c, str) or not c.strip() for c in concepts) or len(set(concepts)) != len(concepts)):
        raise ValueError('Concept selection must be nonempty unique names, or None for all')
    if training_design not in {'target_aware','knowledge_first'} or type(design_target_candidates) is not int or design_target_candidates < 1:
        raise ValueError('Invalid training design or target candidate budget')
    if any(type(v) is not int or v < 1 for v in
           (training_sample_goal, reference_batch_size, max_target_cycles, max_training_attempts)):
        raise ValueError('Training sample goal and loop budgets must be positive integers')
    search = {"image_ref": None,
              "max_metadata_records": None,
              "metadata_candidates": 60, "external_providers": ["commons"],
              "commons_url": "https://commons.wikimedia.org/w/api.php", "commons_thumbnail_width": 0,
              "searxng_url": "http://127.0.0.1:8080", "timeout_s": 15, "trust_env": True,
              "max_download_bytes": 20_000_000, "max_external_queries": 2,
              "external_results_per_query": 4, "max_external_downloads": 6}
    if scene_search:
        if set(scene_search) - set(search):
            raise ValueError('Unknown scene search setting')
        search.update(scene_search)
    if (set(search['external_providers']) - {'commons', 'searxng'}
            or any(search[k] < 1 for k in ('metadata_candidates', 'timeout_s', 'max_download_bytes',
                'max_external_queries', 'external_results_per_query', 'max_external_downloads'))
            or (search['max_metadata_records'] is not None and search['max_metadata_records'] < 1)
            or not isinstance(search['commons_thumbnail_width'], int) or search['commons_thumbnail_width'] < 0):
        raise ValueError('Invalid scene search provider/budget')
    return {"mode": mode, 'scene_search': search, "split_registry": read(registry) if registry else None,
            'training_sample_goal': training_sample_goal, 'reference_batch_size': reference_batch_size,
            'max_target_cycles': max_target_cycles, 'max_training_attempts': max_training_attempts,
            'concepts': list(concepts) if concepts is not None else None,
            'target_candidates_per_task': target_candidates_per_task,
            'training_design':training_design, 'design_target_candidates':design_target_candidates,
            'seed': seed, 'max_units': max_units, 'tasks_per_unit': tasks_per_unit,
            'task_types': list(task_types), 'max_context_chars': max_context_chars,
            'max_reference_images': max_reference_images, 'max_source_images': max_source_images,
            "author": {"backend": mode, "model": author_model or (DEFAULT_MODEL["model"] if mode == "local" else "external"),
                       "reasoning_effort": author_effort},
            "model": {**DEFAULT_MODEL, "max_calls": max_calls}}


def main():
    branch = "training"
    parser = argparse.ArgumentParser(description=f"V2 independent {branch} pipeline (demiflow notebook graph)")
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument('--knowledge-runs', nargs='*', default=[])
    parser.add_argument('--visual-runs', nargs='*', default=[],
                        help='Independent visual publication runs/releases; combined per concept with articles')
    parser.add_argument("--split-registry", type=Path, help='Existing formal-test reservation; omitted means development only')
    parser.add_argument('--concepts', nargs='+', help='Published concept scope; omitted means all')
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--max-units', type=int, default=2)
    parser.add_argument('--training-design', choices=['target_aware','knowledge_first'], default='target_aware')
    parser.add_argument('--training-sample-goal', type=int, default=1)
    parser.add_argument('--reference-batch-size', type=int, default=4)
    parser.add_argument('--max-target-cycles', type=int, default=2)
    parser.add_argument('--max-training-attempts', type=int, default=100)
    parser.add_argument('--design-target-candidates', type=int, default=4)
    parser.add_argument('--target-candidates-per-task', type=int, default=1, help='Training: bounded independent target proposals per frozen task')
    parser.add_argument('--tasks-per-unit', type=int, default=1)
    parser.add_argument('--task-types', choices=['t2i', 'edit'], nargs='+', default=['t2i', 'edit'])
    parser.add_argument("--mode", choices=["offline", "local"], default="offline")
    parser.add_argument("--through", choices=['knowledge', 'training_materials', 'attempt_materials', 'design', 'candidates', 'edit_search', 'edit_source', 'edit_external_search', 'edit_external_source', "construct", "validate", "review", "retrieve", "bind_inputs", "targets", "review_targets", "export"], default="export")
    parser.add_argument('--image-ref', type=Path, help='JSON configuration containing one fixed raw image DatasetRef')
    parser.add_argument('--external-providers', choices=['commons', 'searxng'], nargs='*', default=['commons'])
    parser.add_argument('--searxng-url', default='http://127.0.0.1:8080')
    parser.add_argument('--max-context-chars', type=int, default=60000)
    parser.add_argument('--max-reference-images', type=int, default=16)
    parser.add_argument("--max-calls", type=int, default=12)
    parser.add_argument("--author-model", help="Offline author identity recorded in the frozen run")
    parser.add_argument("--author-effort", help="Offline author reasoning effort")
    parser.add_argument("--no-notebook", action="store_true")
    args = parser.parse_args()
    if args.max_calls < 1:
        parser.error("--max-calls must be positive")
    if branch == "training" and args.through in {"knowledge", "retrieve"}:
        parser.error("Training stages use training_materials and bind_inputs, not knowledge/retrieve")
    result = load_pipeline(branch)(args.run, args.knowledge_runs,
        config(args.mode, args.split_registry, args.max_calls, args.author_model, args.author_effort,
               training_sample_goal=args.training_sample_goal, reference_batch_size=args.reference_batch_size,
               max_target_cycles=args.max_target_cycles, max_training_attempts=args.max_training_attempts,
               training_design=args.training_design, design_target_candidates=args.design_target_candidates, target_candidates_per_task=args.target_candidates_per_task, concepts=args.concepts, seed=args.seed, max_units=args.max_units, tasks_per_unit=args.tasks_per_unit,
               task_types=args.task_types, max_context_chars=args.max_context_chars,
               max_reference_images=args.max_reference_images,
               scene_search={'image_ref': read(args.image_ref) if args.image_ref else None,
                             'external_providers': args.external_providers, 'searxng_url': args.searxng_url}),
        through=args.through, visual_runs=(args.visual_runs or None))
    if not args.no_notebook:
        from curation.preparation.review_notebooks import write_review
        write_review(args.run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
