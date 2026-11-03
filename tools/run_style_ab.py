#!/usr/bin/env python3
"""Run frozen, paired native-Pi trials; retain every predeclared attempt.

The two conditions share an absolute working directory, input hashes and seed.
Outputs are archived after each condition, without rewriting the original logs.
This script never publishes to the qualified training dataset.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import threading

from ppt_rl.inference import run_inference
from ppt_rl.io import read_json, sha256, write_json


def utc():
    return datetime.now(timezone.utc).isoformat()


def rooted(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Plan path escapes experiment root: ' + str(relative))
    return path


def verify_pair(root, pair):
    source = rooted(root, pair['materials_dir'])
    actual = {}
    for path in sorted(source.rglob('*')):
        if path.is_symlink():
            raise ValueError('Input symlinks are not allowed: ' + str(path))
        if path.is_file():
            actual[path.relative_to(source).as_posix()] = sha256(path)
    expected = {f['file']: f['sha256'] for f in pair['content_files']}
    if not expected or actual != expected:
        raise ValueError('Content manifest mismatch for ' + pair['pair_id'])
    for field in ['style_template', 'style_pdf']:
        path = rooted(root, pair[field]['file'])
        if not path.is_file() or sha256(path) != pair[field]['sha256']:
            raise ValueError('Style reference mismatch: ' + str(path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--node', required=True)
    parser.add_argument('--parallel-pairs', type=int, default=4)
    parser.add_argument('--resume', action='store_true', help='Skip archived attempts; never overwrite an active attempt')
    args = parser.parse_args()
    root = args.root.resolve()
    plan_path = args.plan.resolve()
    plan_hash = sha256(plan_path)
    plan = read_json(plan_path)
    pairs = plan['pairs']
    if not 1 <= args.parallel_pairs <= 16:
        raise ValueError('parallel-pairs must be between 1 and 16')
    names = [pair['pair_id'] for pair in pairs]
    if len(set(names)) != len(names) or any(not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', name) for name in names):
        raise ValueError('Pair IDs must be unique safe path components')
    for pair in pairs:
        if sorted(pair['order']) != ['baseline', 'template']:
            raise ValueError('Each pair must declare exactly one attempt per condition')
        verify_pair(root, pair)
        if (root / 'active' / pair['pair_id']).exists():
            raise ValueError('Existing active attempt requires inspection, not automatic replacement')
        if not args.resume and (root / 'runs' / pair['pair_id']).exists():
            raise ValueError('Existing archived attempts; inspect or use --resume')
    checkout = Path(__file__).resolve().parents[1]
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=checkout, text=True).strip()
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=checkout, text=True).strip():
        raise ValueError('Commit the inference code before starting a frozen experiment')
    lock = threading.Lock()
    state = {'started_utc': utc(), 'plan_sha256': plan_hash, 'code_commit': commit,
             'planned_attempts': len(pairs) * 2, 'parallel_pairs': args.parallel_pairs,
             'qualified_dataset_upload': False, 'attempts': {}}
    if args.resume and (root / 'status.json').is_file():
        previous = read_json(root / 'status.json')
        if previous.get('plan_sha256') != plan_hash or previous.get('code_commit') != commit:
            raise ValueError('Resume requires the same frozen plan and inference commit')
        state['started_utc'] = previous['started_utc']
        state['attempts'] = previous['attempts']

    def checkpoint(kind, **fields):
        with lock:
            item = {'at_utc': utc(), 'event': kind, **fields}
            with (root / 'experiment-events.jsonl').open('a') as stream:
                stream.write(json.dumps(item, ensure_ascii=False) + '\n')
            state['updated_utc'] = item['at_utc']
            write_json(root / 'status.json', state)
            print(json.dumps(item, ensure_ascii=False), flush=True)

    def run_pair(pair):
        pair_id = pair['pair_id']
        active = root / 'active' / pair_id
        for condition in pair['order']:
            target = root / 'runs' / pair_id / condition
            key = pair_id + '/' + condition
            if target.exists():
                if not args.resume or not (target / 'result.json').is_file():
                    raise ValueError('Refuse to overwrite ' + str(target))
                continue
            if sha256(plan_path) != plan_hash:
                raise ValueError('Experiment plan changed after freezing')
            verify_pair(root, pair)
            with lock:
                state['attempts'][key] = {'status': 'running', 'started_utc': utc(), 'active_dir': str(active)}
            checkpoint('attempt_started', pair_id=pair_id, task_id=pair['task_id'], condition=condition)
            sampling = dict(plan['sampling'], base_seed=pair['base_seed'])
            result = run_inference(
                rooted(root, pair['materials_dir']), active, model=plan['model'], endpoint=plan['endpoint'],
                query=pair['query'], sampling=sampling, max_turns=plan['max_turns'], node=args.node,
                style_template=rooted(root, pair['style_template']['file']) if condition == 'template' else None,
                style_pdf=rooted(root, pair['style_pdf']['file']) if condition == 'template' else None,
            )
            # Move rather than rewrite: original native logs retain their historical cwd.
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(active), str(target))
            artifact = target / 'work/outputs/presentation.pptx'
            if artifact.is_file() and result.get('pptx_sha256') != sha256(artifact):
                raise ValueError('Archived PPTX hash mismatch: ' + key)
            write_json(target / 'archive-location.json', {
                'original_working_directory': str(active / 'work'), 'archived_directory': str(target),
                'pptx_relative_path': 'work/outputs/presentation.pptx' if artifact.is_file() else None,
                'note': 'Absolute paths in original logs refer to the historical active directory. Logs are not rewritten.',
            })
            with lock:
                state['attempts'][key] = {
                    'status': 'completed' if result['success'] else 'failed',
                    'archive_dir': str(target), 'agent_state': result.get('agent_state'),
                    'success': result['success'], 'pages': result.get('pages'),
                    'elapsed_seconds': result['elapsed_seconds'], 'tool_calls': result.get('tool_calls'),
                    'error': result.get('error'), 'finished_utc': result['finished_utc'],
                }
            checkpoint('attempt_finished', pair_id=pair_id, condition=condition,
                       success=result['success'], agent_state=result.get('agent_state'), pages=result.get('pages'))
        return pair_id

    checkpoint('experiment_started')
    failures = []
    with ThreadPoolExecutor(max_workers=args.parallel_pairs) as pool:
        futures = {pool.submit(run_pair, pair): pair['pair_id'] for pair in pairs}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as error:
                failures.append({'pair_id': futures[future], 'error': type(error).__name__ + ': ' + str(error)})
                checkpoint('controller_error', **failures[-1])
    with lock:
        state['finished_utc'] = utc()
        state['controller_errors'] = failures
        state['completed_attempts'] = sum(x['status'] in ['completed', 'failed'] for x in state['attempts'].values())
        state['successful_attempts'] = sum(x.get('success', False) for x in state['attempts'].values())
    checkpoint('experiment_finished')
    return 1 if failures or state['completed_attempts'] != len(pairs) * 2 else 0


if __name__ == '__main__':
    raise SystemExit(main())
