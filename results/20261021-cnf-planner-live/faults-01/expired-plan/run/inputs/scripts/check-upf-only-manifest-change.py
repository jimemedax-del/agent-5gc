#!/usr/bin/env python3
"""Fail closed unless two Helm manifests differ only in the UPF image fields."""
import argparse
import copy
import json
from pathlib import Path

import yaml


def resources(path):
    result = {}
    for obj in yaml.safe_load_all(Path(path).read_text()):
        if not obj:
            continue
        key = (obj['apiVersion'], obj['kind'], obj['metadata'].get('namespace', ''),
               obj['metadata']['name'])
        if key in result:
            raise ValueError(f'Duplicate resource: {key}')
        result[key] = obj
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before')
    parser.add_argument('after')
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    before, after = resources(args.before), resources(args.after)
    if set(before) != set(after):
        raise ValueError('Resource set changed')
    changed = []
    for key, old in before.items():
        new = copy.deepcopy(after[key])
        if old == new:
            continue
        if key[1] != 'Deployment' or key[3] != 'free5gc-helm-free5gc-upf-upf':
            raise ValueError(f'Non-UPF resource changed: {key}')
        old_containers = old['spec']['template']['spec']['containers']
        new_containers = new['spec']['template']['spec']['containers']
        if len(old_containers) != 1 or len(new_containers) != 1:
            raise ValueError('Unexpected UPF container set')
        container = new_containers[0]
        if container['image'] != args.image or container['imagePullPolicy'] != 'Never':
            raise ValueError('Unexpected patched image or pull policy')
        changed.append({'resource': key[3], 'oldImage': old_containers[0]['image'],
                        'newImage': container['image']})
        for field in ('image', 'imagePullPolicy'):
            container[field] = old_containers[0][field]
        if old != new:
            raise ValueError('UPF fields other than image/pull policy changed')
    if len(changed) != 1:
        raise ValueError('Expected exactly one changed UPF Deployment')
    print(json.dumps({'checkedResources': len(before), 'changes': changed}, indent=2))


if __name__ == '__main__':
    main()
