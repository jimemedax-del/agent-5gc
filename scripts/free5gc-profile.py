#!/usr/bin/env python3
"""One fixed, digest-locked three-node deployment profile. No arbitrary Values."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import yaml

ROOT = Path(__file__).resolve().parent.parent
PROFILE_ID = 'free5gc-3node-no-multus-v1'
PROFILE_PATH = ROOT / 'infra' / 'profiles' / f'{PROFILE_ID}.json'
CONTROLLERS = {'Deployment', 'StatefulSet'}
# free5GC NF processes attempt NRF registration at startup.  A Pod being Ready
# only proves the process is running; it does not prove that NRF/MongoDB was
# already ready when that one-shot registration was attempted.
NRF_REREGISTRATION_ORDER = (
    'free5gc-helm-free5gc-ausf-ausf',
    'free5gc-helm-free5gc-udr-udr',
    'free5gc-helm-free5gc-udm-udm',
    'free5gc-helm-free5gc-pcf-pcf',
    'free5gc-helm-free5gc-nssf-nssf',
    'free5gc-helm-free5gc-nef-nef',
    'free5gc-helm-free5gc-chf-chf',
    'free5gc-helm-free5gc-smf-smf',
    # AMF is last: it discovers AUSF/UDM/SMF through NRF for UE registration.
    'free5gc-helm-free5gc-amf-amf',
)


def run(command, timeout=60):
    proc = subprocess.run([str(x) for x in command], capture_output=True, text=True,
                          timeout=timeout)
    if proc.returncode:
        # Do not echo full rendered manifests or authenticated API responses.
        raise RuntimeError(f'{command[0]} failed ({proc.returncode}): {proc.stderr[-1500:]}')
    return proc.stdout


def save_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def sha_lf(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def repo_path(relative):
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT) or Path(relative).is_absolute():
        raise ValueError('Profile path escapes repository')
    return path


def load_profile():
    profile = json.loads(PROFILE_PATH.read_text(encoding='utf-8'))
    if profile['schemaVersion'] != 1 or profile['profileId'] != PROFILE_ID:
        raise ValueError('Unsupported profile')
    for name, expected in profile['fileHashesLF'].items():
        if sha_lf(repo_path(name)) != expected:
            raise ValueError(f'Controlled file changed: {name}; review and version the profile')
    required = [profile['chart']['patch'], profile['subscriber']['file']]
    required += [f for release in profile['releases'] for f in release['values']]
    if any(f not in profile['fileHashesLF'] for f in required):
        raise ValueError('Unlocked profile input')
    return profile


def normalize_image(image):
    # These profiles use Docker Hub only; normalize short names and tag@digest.
    if image.startswith('docker.io/'):
        image = image[len('docker.io/'):]
    if '/' not in image:
        image = 'library/' + image
    if '@' in image:
        name, digest = image.split('@', 1)
        name = name.rsplit(':', 1)[0] if ':' in name.rsplit('/', 1)[-1] else name
        image = name + '@' + digest
    return 'docker.io/' + image


def locked_image(item):
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', item['digest']):
        raise ValueError('Invalid image digest')
    source = normalize_image(item['source']).split('@')[0]
    name = source.rsplit(':', 1)[0] if ':' in source.rsplit('/', 1)[-1] else source
    return name + '@' + item['digest']


def image_index(profile):
    result = {}
    for item in profile['images']:
        for name in (normalize_image(item['source']), locked_image(item)):
            if name in result and result[name] != item:
                raise ValueError('Conflicting image lock')
            result[name] = item
    return result


def lock_documents(documents, profile):
    index = image_index(profile)
    result = copy.deepcopy(documents)
    for obj in result:
        if obj['kind'] not in CONTROLLERS:
            continue
        spec = obj['spec']['template']['spec']
        for container in (spec.get('containers') or []) + (spec.get('initContainers') or []):
            key = normalize_image(container['image'])
            if key not in index:
                raise ValueError(f'Image not in whitelist: {key}')
            item = index[key]
            container['image'] = locked_image(item)
            container['imagePullPolicy'] = item.get('pullPolicy', 'IfNotPresent')
    return result


def parse_documents(text):
    return [obj for obj in yaml.safe_load_all(text) if obj]


def validate_documents(documents, profile, complete=True):
    workloads, objects = {}, set()
    locks = image_index(profile)
    for obj in documents:
        key = (obj['kind'], obj['metadata'].get('namespace', ''), obj['metadata']['name'])
        if key in objects:
            raise ValueError(f'Duplicate resource: {key}')
        objects.add(key)
        if obj['metadata'].get('namespace') not in (None, '', profile['namespace']):
            raise ValueError(f'Unexpected namespace: {key}')
        if obj['kind'] in {'HorizontalPodAutoscaler', 'DaemonSet', 'Job', 'CronJob', 'Pod'}:
            raise ValueError(f'Unsupported resource in fixed profile: {key}')
        if obj['kind'] not in CONTROLLERS:
            continue
        name = obj['metadata']['name']
        if name not in profile['workloads']:
            raise ValueError(f'Unexpected workload: {name}')
        expected = profile['workloads'][name]
        template = obj['spec']['template']
        spec = template['spec']
        if obj['spec'].get('replicas', 1) != 1:
            raise ValueError(f'Only one replica allowed: {name}')
        if spec.get('nodeSelector') != profile['roles'][expected['role']]:
            raise ValueError(f'Wrong placement: {name}')
        if spec.get('hostNetwork') or 'k8s.v1.cni.cncf.io/networks' in (template['metadata'].get('annotations') or {}):
            raise ValueError(f'Unexpected secondary/host network: {name}')
        if len(spec.get('containers') or []) != 1:
            raise ValueError(f'Expected exactly one application container: {name}')
        for c in (spec.get('containers') or []) + (spec.get('initContainers') or []):
            item = locks.get(normalize_image(c['image']))
            if not item or normalize_image(c['image']) != locked_image(item):
                raise ValueError(f'Unpinned/unapproved image: {name}')
            if item['role'] != expected['role']:
                raise ValueError(f'Image placed in wrong role: {name}')
        workloads[name] = expected
    if complete and set(workloads) != set(profile['workloads']):
        raise ValueError('Missing required workloads: ' + ', '.join(sorted(set(profile['workloads']) - set(workloads))))
    return {'resources': len(objects), 'workloads': len(workloads)}


def helm_binary():
    candidate = os.environ.get('HELM') or shutil.which('helm') or str(Path.home() / '.local/bin/helm')
    if not Path(candidate).is_file():
        raise RuntimeError('Helm 3 is required; set HELM to its executable path')
    return candidate


def chart_hash(chart):
    files = run(['git', '-C', chart, 'ls-files', '-z', 'charts']).split('\0')
    h = hashlib.sha256()
    for name in sorted(f for f in files if f):
        h.update(name.encode() + b'\0')
        h.update(bytes.fromhex(sha_lf(chart / name)))
    return h.hexdigest()


def prepare_chart(args, profile):
    chart = Path(args.chart_dir or ROOT / '.work' / PROFILE_ID / 'chart').resolve()
    if not chart.exists():
        chart.parent.mkdir(parents=True, exist_ok=True)
        run(['git', '-c', 'core.autocrlf=false', 'clone', '--no-checkout', '--no-hardlinks',
             args.chart_source or profile['chart']['repository'], chart], timeout=180)
        run(['git', '-C', chart, '-c', 'core.autocrlf=false', 'checkout', '--detach', profile['chart']['commit']])
        run(['git', '-C', chart, 'apply', '--check', repo_path(profile['chart']['patch'])])
        run(['git', '-C', chart, 'apply', repo_path(profile['chart']['patch'])])
    if run(['git', '-C', chart, 'rev-parse', 'HEAD']).strip() != profile['chart']['commit']:
        raise ValueError('Chart is at the wrong commit; refusing to reset it')
    if run(['git', '-C', chart, 'ls-files', '--others', '--exclude-standard', 'charts']).strip():
        raise ValueError('Untracked Chart inputs are not allowed')
    if chart_hash(chart) != profile['chart']['treeSha256LF']:
        raise ValueError('Chart content differs from fixed baseline')
    return chart


def render(args, profile):
    out = Path(args.out or ROOT / '.work' / PROFILE_ID / 'render').resolve()
    out.mkdir(parents=True, exist_ok=True)
    chart, helm, docs = prepare_chart(args, profile), helm_binary(), []
    report = {'profileId': PROFILE_ID, 'profileSha256LF': sha_lf(PROFILE_PATH),
              'chartCommit': profile['chart']['commit'], 'chartTreeSha256LF': chart_hash(chart), 'releases': []}
    for release in profile['releases']:
        values = [part for f in release['values'] for part in ('-f', repo_path(f))]
        run([helm, 'lint', chart / release['chartPath'], *values])
        raw = run([helm, 'template', release['name'], chart / release['chartPath'],
                   '-n', profile['namespace'], '--is-upgrade', '--skip-tests', *values])
        locked = lock_documents(parse_documents(raw), profile)
        validate_documents(locked, profile, complete=False)
        docs += locked
        path = out / (release['name'] + '.yaml')
        path.write_text(yaml.safe_dump_all(locked, sort_keys=False), encoding='utf-8')
        semantic_hash = hashlib.sha256(json.dumps(locked, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        report['releases'].append({'name': release['name'], 'semanticSha256': semantic_hash, 'manifest': str(path)})
    report.update(validate_documents(docs, profile))
    save_json(out / 'render-report.json', report)
    return chart, out, docs, report


def cluster_check(profile):
    nodes = json.loads(run(['kubectl', 'get', 'nodes', '-o', 'json']))['items']
    roles = {}
    for role, labels in profile['roles'].items():
        matched = [n for n in nodes if all(n['metadata'].get('labels', {}).get(k) == v for k, v in labels.items())]
        if len(matched) != 1:
            raise ValueError(f'Need exactly one node for role {role}')
        node = matched[0]
        platform = node['metadata'].get('labels', {})
        if platform.get('kubernetes.io/os') != 'linux' or platform.get('kubernetes.io/arch') != 'amd64':
            raise ValueError(f'Node must be linux/amd64: {role}')
        if node['spec'].get('unschedulable') or not any(c['type'] == 'Ready' and c['status'] == 'True' for c in node['status']['conditions']):
            raise ValueError(f'Node not schedulable/Ready: {role}')
        if any(t['effect'] in {'NoSchedule', 'NoExecute'} for t in node['spec'].get('taints', [])):
            raise ValueError(f'Node has unsupported taints: {role}')
        roles[role] = {'node': node['metadata']['name'], 'internalIP': next(a['address'] for a in node['status']['addresses'] if a['type'] == 'InternalIP')}
    if len({entry['node'] for entry in roles.values()}) != len(profile['roles']):
        raise ValueError('Three roles must use three distinct nodes')
    run(['kubectl', 'get', 'storageclass', 'local-path', '-o', 'json'])
    return {'roles': roles, 'prerequisitesNotAutoInstalled': ['gtp5g v0.9.5 and allowed ip_forward sysctl',
            'digest image cache/aliases on each target node', 'test subscriber and existing MongoDB data',
            'node resources, SCTP and network connectivity']}


def compare_current(profile, docs):
    old = []
    for release in profile['releases']:
        old += parse_documents(run([helm_binary(), 'get', 'manifest', release['name'], '-n', profile['namespace']]))
    # Compare all resources after ONLY the controlled image/pull-policy transformation.
    original = old
    old = lock_documents(old, profile)
    def mapping(items):
        return {(o['apiVersion'], o['kind'], o['metadata'].get('namespace', ''), o['metadata']['name']): o for o in items}
    if mapping(old) != mapping(docs):
        raise ValueError('Template differs from running releases beyond controlled image pins/policies')
    return {'equivalentAfterImageLock': True, 'newDigestTemplateDeployed': mapping(original) == mapping(docs)}


def verify(profile, allow_tag_baseline=False):
    cluster = cluster_check(profile)
    ns = profile['namespace']
    controllers = json.loads(run(['kubectl', '-n', ns, 'get', 'deployments,statefulsets', '-o', 'json']))['items']
    pods = json.loads(run(['kubectl', '-n', ns, 'get', 'pods', '-o', 'json']))['items']
    index, checks = image_index(profile), []
    for name, item in profile['workloads'].items():
        controller = next((c for c in controllers if c['metadata']['name'] == name), None)
        if controller is None or controller['spec'].get('replicas', 1) != 1:
            raise ValueError(f'Missing/incorrect replicas: {name}')
        labels = controller['spec']['selector']['matchLabels']
        matched = [p for p in pods if not p['metadata'].get('deletionTimestamp') and
                   all(p['metadata'].get('labels', {}).get(k) == v for k, v in labels.items())]
        if len(matched) != 1:
            raise ValueError(f'Need one Pod: {name}')
        pod = matched[0]
        if pod['spec']['nodeName'] != cluster['roles'][item['role']]['node'] or not any(c['type'] == 'Ready' and c['status'] == 'True' for c in pod['status'].get('conditions', [])):
            raise ValueError(f'Wrong node/not Ready: {name}')
        statuses = {c['name']: c for c in pod['status'].get('containerStatuses', []) + pod['status'].get('initContainerStatuses', [])}
        for c in pod['spec'].get('containers', []) + pod['spec'].get('initContainers', []):
            lock = index.get(normalize_image(c['image']))
            if not lock or lock['role'] != item['role']:
                raise ValueError(f'Unknown live image: {name}')
            if not allow_tag_baseline and normalize_image(c['image']) != locked_image(lock):
                raise ValueError(f'Live workload still uses tag: {name}')
            image_id = statuses.get(c['name'], {}).get('imageID', '')
            if image_id.split('@')[-1] not in {lock['digest'], lock.get('configDigest')}:
                raise ValueError(f'Live digest differs from lock: {name}')
        checks.append({'workload': name, 'node': pod['spec']['nodeName'], 'pod': pod['metadata']['name']})
    # Check actual host kernel visibility through the existing UPF, not label alone.
    modules = run(['kubectl', '-n', ns, 'exec', 'deployment/free5gc-helm-free5gc-upf-upf', '--', 'cat', '/proc/modules'])
    if not re.search(r'^gtp5g ', modules, re.M):
        raise ValueError('UPF host kernel module not loaded')
    for deployment, phrase in [('ueransim-gnb', 'NG Setup procedure is successful'),
                               ('ueransim-ue', 'Initial Registration is successful'),
                               ('ueransim-ue', 'PDU Session establishment is successful')]:
        log = run(['kubectl', '-n', ns, 'logs', 'deployment/' + deployment, '--tail=400'])
        if phrase not in log:
            raise ValueError(f'Business log missing: {phrase}')
    target = cluster['roles'][profile['verification']['targetNodeRole']]['internalIP']
    ping = run(['kubectl', '-n', ns, 'exec', 'deployment/ueransim-ue', '--', 'ping', '-I',
                profile['verification']['tun'], '-c', str(profile['verification']['pingCount']), '-i', '0.2', '-W', '2', target])
    if '0% packet loss' not in ping or '100% packet loss' in ping:
        raise ValueError('Tunnel packet loss')
    return {'workloads': checks, 'allowTagBaseline': allow_tag_baseline, 'tunnelTarget': target, 'ping': ping}


def stabilize_nrf_registrations(namespace):
    """Re-register NRF-dependent NFs after a complete core Release upgrade."""
    for deployment in NRF_REREGISTRATION_ORDER:
        run(['kubectl', '-n', namespace, 'rollout', 'restart', 'deployment/' + deployment])
        run(['kubectl', '-n', namespace, 'rollout', 'status', 'deployment/' + deployment,
             '--timeout=120s'], timeout=130)


def apply(args, profile, chart, out):
    if not args.confirm_disruption:
        raise ValueError('apply requires --confirm-disruption; it rebuilds UE sessions')
    if not os.access(__file__, os.X_OK):
        raise ValueError('chmod +x scripts/free5gc-profile.py before Helm post-rendering')
    ns, helm = profile['namespace'], helm_binary()
    # Require the existing DB/subscriber environment; fresh installation is a separate workflow.
    run([helm, 'status', 'free5gc-helm', '-n', ns, '-o', 'json'])
    run([helm, 'status', 'ueransim', '-n', ns, '-o', 'json'])
    for release in profile['releases']:
        previous = run([helm, 'get', 'values', release['name'], '-n', ns, '-o', 'yaml'])
        (out / (release['name'] + '-before-values.yaml')).write_text(previous, encoding='utf-8')
        (out / (release['name'] + '-before-history.json')).write_text(run([helm, 'history', release['name'], '-n', ns, '-o', 'json']), encoding='utf-8')
    original = {}
    for name in ['ueransim-ue', 'ueransim-gnb', 'free5gc-helm-free5gc-upf-upf']:
        obj = json.loads(run(['kubectl', '-n', ns, 'get', 'deployment', name, '-o', 'json']))
        original[name] = obj['spec'].get('replicas', 1)
    succeeded = False
    try:
        for name, label in [('ueransim-ue', 'component=ue'), ('ueransim-gnb', 'component=gnb'),
                            ('free5gc-helm-free5gc-upf-upf', 'nf=upf')]:
            run(['kubectl', '-n', ns, 'scale', 'deployment/' + name, '--replicas=0'])
            run(['kubectl', '-n', ns, 'wait', '--for=delete', 'pod', '-l', label, '--timeout=60s'], timeout=70)
        for release in profile['releases']:
            values = [p for f in release['values'] for p in ('-f', repo_path(f))]
            print(f'Upgrading {release["name"]}', flush=True)
            run([helm, 'upgrade', release['name'], chart / release['chartPath'], '-n', ns,
                 '--reset-values', *values, '--post-renderer', Path(__file__).resolve(),
                 '--post-renderer-args', 'post-render', '--atomic', '--wait', '--timeout', '10m'], timeout=630)
            if release['name'] == 'free5gc-helm':
                stabilize_nrf_registrations(ns)
        for _ in range(12):
            try:
                result = verify(profile)
                save_json(out / 'verify-report.json', result)
                succeeded = True
                break
            except (ValueError, RuntimeError):
                time.sleep(5)
        if not succeeded:
            raise RuntimeError('Helm upgrade finished but business verification failed; consult saved rollback Values')
    finally:
        if not succeeded:
            # Restore manually scaled workloads only; do not claim cross-release atomic rollback.
            for name, count in original.items():
                try:
                    run(['kubectl', '-n', ns, 'scale', 'deployment/' + name, f'--replicas={count}'])
                except RuntimeError:
                    pass
    return {'applied': True, 'crossReleaseAtomic': False, 'subscriberModified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['render', 'check', 'compare-current', 'verify', 'apply', 'post-render', 'image-commands'])
    parser.add_argument('--chart-source', help='Optional existing Git repository for offline clone')
    parser.add_argument('--chart-dir', help='Dedicated fixed Chart checkout; never resets other directories')
    parser.add_argument('--out', help='Generated manifests/reports directory (default .work/)')
    parser.add_argument('--allow-tag-baseline', action='store_true', help='Audit existing tag deployment before digest-template upgrade')
    parser.add_argument('--confirm-disruption', action='store_true')
    parser.add_argument('--role', choices=['control', 'user', 'ran'])
    args = parser.parse_args()
    profile = load_profile()
    if args.command == 'post-render':
        docs = lock_documents(parse_documents(sys.stdin.read()), profile)
        validate_documents(docs, profile, complete=False)
        sys.stdout.write(yaml.safe_dump_all(docs, sort_keys=False))
        return
    if args.command == 'image-commands':
        for item in profile['images']:
            if args.role and item['role'] != args.role:
                continue
            print(f'# Node role: {item["role"]}; import the verified image archive first')
            print('sudo k3s ctr images tag ' + normalize_image(item['source'].split('@')[0]) + ' ' + locked_image(item))
        return
    if args.command == 'verify':
        result = verify(profile, args.allow_tag_baseline)
        if args.out:
            Path(args.out).mkdir(parents=True, exist_ok=True)
            save_json(Path(args.out) / 'verify-report.json', result)
    else:
        chart, out, docs, result = render(args, profile)
        if args.command in {'check', 'compare-current', 'apply'}:
            result['cluster'] = cluster_check(profile)
        if args.command in {'compare-current', 'apply'}:
            result['comparison'] = compare_current(profile, docs)
        if args.command == 'apply':
            result['deployment'] = apply(args, profile, chart, out)
            result['comparison']['newDigestTemplateDeployed'] = True
        save_json(out / 'render-report.json', result)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, KeyError, OSError, subprocess.TimeoutExpired) as exc:
        print(f'PROFILE ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
