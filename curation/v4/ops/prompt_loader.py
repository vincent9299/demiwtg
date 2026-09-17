"""Read versioned prompt source from code, never from a run directory."""
from pathlib import Path
import yaml

def load_instruction(name):
    value=yaml.safe_load((Path(__file__).parent/'prompts'/f'{name}.yaml').read_text())
    if not isinstance(value.get('instruction'),str):
        raise ValueError(f'Invalid prompt source: {name}')
    return value['instruction']
