#!/usr/bin/env python3
"""D1 offline intent contract and conservative rule baseline; no cluster access."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / 'infra/cnfs/requirements-contract.json'
FIELDS = ('direction', 'min_bandwidth_mbps', 'max_bandwidth_mbps', 'rtt_p95_max_ms',
          'one_way_p95_max_ms', 'loss_max_percent', 'priority', 'valid_for_seconds',
          'deadline_seconds', 'measurement_window_seconds')
RATE_UNITS = {'Gbps': 1000, 'Gbit/s': 1000, 'Mbps': 1, 'Mbit/s': 1,
              'kbps': .001, 'Kbps': .001, 'kbit/s': .001, 'GB/s': 8000, 'MB/s': 8, 'kB/s': .008}
TIME_UNITS = {'秒': 1, 's': 1, '分钟': 60, 'min': 60, '小时': 3600, 'h': 3600}
NUMBER = r'([-+]?\d+(?:\.\d+)?)'
NEGATION = re.compile(r'不是|并非|不要|无需|不需要|不要求|非(?:视频|后台|远程)|\bnot\b', re.I)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    def invalid(value):
        raise ValueError('Nonfinite JSON number: ' + value)
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, 'Duplicate JSON key: ' + key)
            value[key] = item
        return value
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), parse_constant=invalid, object_pairs_hook=unique)


def contract():
    value = read_json(CONTRACT)
    require(value['contractVersion'] == 'cnf-requirements-v1' and tuple(value['fields']) == FIELDS,
            'Unknown requirement contract')
    require([c['id'] for c in value['categories']] == ['interactive', 'streaming', 'bulk'], 'Unknown taxonomy')
    require(not value['rules']['defaultsAllowed'] and not value['rules']['flowMetadataUsedForClassification'] and
            not value['rules']['observationsUsedAsRequestedTargets'], 'Unreviewed implicit defaults or label leakage')
    return value


def validate_value(name, value, spec):
    rule = spec['fields'][name]
    if rule['type'] == 'enum':
        require(isinstance(value, str) and value in rule['values'], 'Invalid ' + name)
    else:
        require(type(value) in [int, float] and math.isfinite(value), 'Nonfinite/non-number ' + name)
        for key, predicate in [('minimum', lambda x, y: x >= y), ('maximum', lambda x, y: x <= y),
                               ('exclusiveMinimum', lambda x, y: x > y)]:
            require(key not in rule or predicate(value, rule[key]), 'Out of range ' + name)


def validate_input(value, spec):
    require(type(value) is dict and set(value) <= {'request_id', 'description', 'subject', 'context', 'requested', 'flow', 'observations'},
            'Unknown request fields (labels, backend choices and commands are not accepted)')
    require(isinstance(value.get('request_id'), str) and 0 < len(value['request_id']) <= 128, 'request_id required')
    require(isinstance(value.get('description', ''), str) and len(value.get('description', '')) <= 12000, 'Invalid description')
    require(type(value.get('subject', {})) is dict and set(value.get('subject', {})) <= {'user_id', 'session_id'}, 'Invalid subject')
    for key, item in value.get('subject', {}).items():
        require(isinstance(item, str) and 0 < len(item) <= 128, 'Invalid subject ' + key)
    requested = value.get('requested', {})
    require(type(requested) is dict and set(requested) <= set(FIELDS), 'Unknown structured target')
    for name, item in requested.items():
        validate_value(name, item, spec)
    context = value.get('context', {})
    require(type(context) is dict and set(context) <= {'interaction', 'delivery'}, 'Only business facts are accepted as context')
    require(context.get('interaction', 'unknown') in ['closed_loop', 'none', 'unknown'] and
            context.get('delivery', 'unknown') in ['continuous_playback', 'background_transfer', 'unknown'], 'Invalid context fact')
    flow = value.get('flow', {})
    require(type(flow) is dict and set(flow) <= {'protocol', 'source_port', 'destination_port'}, 'Invalid flow hint')
    if 'protocol' in flow:
        require(flow['protocol'] in ['TCP', 'UDP'], 'Invalid transport')
    for key in ['source_port', 'destination_port']:
        if key in flow:
            require(type(flow[key]) is int and 1 <= flow[key] <= 65535, 'Invalid port')
    observations = value.get('observations', {})
    require(type(observations) is dict and set(observations) <= {'throughput_mbps', 'rtt_p95_ms', 'loss_percent'}, 'Invalid observations')
    for key, item in observations.items():
        require(type(item) in [int, float] and math.isfinite(item) and item >= 0 and
                (key != 'loss_percent' or item <= 100), 'Invalid observed metric')


def clauses(text):
    # Reject inference inside a negated clause rather than interpreting arbitrary
    # natural-language scope. The conservative abstentions are part of evaluation.
    for match in re.finditer(r'[^，。；;\n]+', text):
        if not NEGATION.search(match[0]):
            yield match[0], match.start()


def classify(value, spec):
    evidence, found = [], set()
    for text, offset in clauses(value.get('description', '')):
        for category in spec['categories']:
            for pattern in category['patterns']:
                for match in re.finditer(pattern, text, re.I):
                    found.add(category['id'])
                    evidence.append({'category': category['id'], 'kind': 'user_text', 'text': match[0],
                                     'start': offset + match.start(), 'end': offset + match.end()})
    context = value.get('context', {})
    facts = [('interaction', 'closed_loop', 'interactive'), ('delivery', 'continuous_playback', 'streaming'),
             ('delivery', 'background_transfer', 'bulk')]
    for key, fact, category in facts:
        if context.get(key) == fact:
            found.add(category)
            evidence.append({'category': category, 'kind': 'application_context', 'path': '/context/' + key, 'value': fact})
    result = next(iter(found)) if len(found) == 1 else None
    level = 'high' if result and any(e['kind'] == 'application_context' for e in evidence) else 'medium' if result else 'low'
    return {'value': result, 'candidates': sorted(found), 'status': 'recognized' if result else 'ambiguous' if found else 'unknown',
            'confidence': {'level': level, 'method': 'deterministic_rule_strength', 'calibrated': False}, 'evidence': evidence}


def extract(value, spec):
    candidates = {name: [] for name in FIELDS}
    issues = []

    def add(name, item, source):
        if type(item) is float and not math.isfinite(item):
            source = {**source, 'invalidNumeric': True}
            item = None
        candidates[name].append({'value': item, **source})

    for name, item in value.get('requested', {}).items():
        add(name, item, {'kind': 'user_structured', 'path': '/requested/' + name})
    rate_unit = '(' + '|'.join(re.escape(x) for x in sorted(RATE_UNITS, key=len, reverse=True)) + ')'
    time_unit = '(' + '|'.join(re.escape(x) for x in sorted(TIME_UNITS, key=len, reverse=True)) + ')'
    patterns = [
        ('min_bandwidth_mbps', r'(?:最低|至少|不低于|保底|(?i:minimum|min))\s*(?:带宽|速率)?\s*[:：=]?\s*' + NUMBER + r'\s*' + rate_unit, RATE_UNITS),
        ('max_bandwidth_mbps', r'(?:最高|最多|不超过|上限|(?i:maximum|max))\s*(?:带宽|速率)?\s*[:：=]?\s*' + NUMBER + r'\s*' + rate_unit, RATE_UNITS),
        ('rtt_p95_max_ms', r'(?:RTT\s*[pP]95|[pP]95\s*RTT)\s*(?:不超过|上限|<=|≤)\s*' + NUMBER + r'\s*(ms|毫秒)', {'ms': 1, '毫秒': 1}),
        ('one_way_p95_max_ms', r'(?:单向时延\s*[pP]95|[pP]95\s*单向时延)\s*(?:不超过|上限|<=|≤)\s*' + NUMBER + r'\s*(ms|毫秒)', {'ms': 1, '毫秒': 1}),
        ('loss_max_percent', r'丢包(?:率)?\s*(?:不超过|上限|<=|≤)\s*' + NUMBER + r'\s*(%)', {'%': 1}),
        ('valid_for_seconds', r'(?:有效期|有效|valid for)\s*[:：=]?\s*' + NUMBER + r'\s*' + time_unit, TIME_UNITS),
        ('measurement_window_seconds', r'(?:统计窗口|测量窗口|window)\s*[:：=]?\s*' + NUMBER + r'\s*' + time_unit, TIME_UNITS),
        ('deadline_seconds', r'在\s*' + NUMBER + r'\s*' + time_unit + r'\s*内完成', TIME_UNITS)]
    for text, offset in clauses(value.get('description', '')):
        if re.search(r'当前|实测|观测|测得', text):
            continue
        for name, pattern, units in patterns:
            for match in re.finditer(pattern, text):
                add(name, float(match[1]) * units[match[2]], {'kind': 'user_text', 'text': match[0],
                    'start': offset + match.start(), 'end': offset + match.end()})
        direction_matches = list(re.finditer(r'上行|上传|uplink|下行|下载|downlink|双向|bidirectional', text, re.I))
        explicit_both = any(m[0].lower() in ['双向', 'bidirectional'] for m in direction_matches)
        for match in direction_matches:
            item = 'bidirectional' if explicit_both else 'uplink' if match[0].lower() in ['上行', '上传', 'uplink'] else 'downlink'
            add('direction', item, {'kind': 'user_text', 'text': match[0], 'start': offset + match.start(), 'end': offset + match.end()})
        for match in re.finditer(r'优先级\s*(?:为|[:：=])?\s*(高|中|低|high|normal|low)', text):
            item = {'高': 'high', '中': 'normal', '低': 'low'}.get(match[1], match[1])
            add('priority', item, {'kind': 'user_text', 'text': match[0], 'start': offset + match.start(), 'end': offset + match.end()})
        if re.search(r'时延|延迟|RTT|latency', text, re.I) and not any(
                re.search(pattern, text) for name, pattern, _ in patterns if name in ['rtt_p95_max_ms', 'one_way_p95_max_ms']):
            issues.append({'code': 'latency_metric_or_percentile_unspecified', 'text': text})
        if re.search(r'丢包|\bloss\b', text, re.I) and not any(
                re.search(pattern, text) for name, pattern, _ in patterns if name == 'loss_max_percent'):
            issues.append({'code': 'loss_target_unspecified_or_unsupported', 'text': text})
        if '优先级' in text and not re.search(r'优先级\s*(?:为|[:：=])?\s*(高|中|低|high|normal|low)', text):
            issues.append({'code': 'priority_expression_unsupported', 'text': text})
        for field, cue in [('min_bandwidth_mbps', r'最低|至少|不低于|保底|(?i:minimum|min)'),
                           ('max_bandwidth_mbps', r'最高|最多|不超过|上限|(?i:maximum|max)')]:
            if re.search(cue, text) and re.search(r'带宽|速率|[kKmMgG](?:i?[bB]|bit)', text) and not any(
                    re.search(pattern, text) for name, pattern, _ in patterns if name == field):
                issues.append({'code': 'bandwidth_expression_unsupported', 'field': field, 'text': text})
        if re.search(r'有效期|valid for', text) and not any(
                re.search(pattern, text) for name, pattern, _ in patterns if name == 'valid_for_seconds'):
            issues.append({'code': 'validity_expression_unsupported', 'text': text})
        if re.search(r'截止|deadline|内完成', text, re.I) and not any(
                re.search(pattern, text) for name, pattern, _ in patterns if name == 'deadline_seconds'):
            issues.append({'code': 'deadline_expression_unsupported', 'text': text})
    fields = {}
    for name, sources in candidates.items():
        invalid = False
        for source in sources:
            try:
                validate_value(name, source['value'], spec)
            except ValueError:
                invalid = True
        distinct = {source['value'] for source in sources}
        resolution = 'invalid' if invalid else 'conflict' if len(distinct) > 1 else 'explicit' if sources else 'unknown'
        fields[name] = {'value': next(iter(distinct)) if resolution == 'explicit' else None, 'resolution': resolution, 'sources': sources}
        if resolution in ['conflict', 'invalid']:
            issues.append({'code': 'field_' + resolution, 'field': name})
    low, high = fields['min_bandwidth_mbps']['value'], fields['max_bandwidth_mbps']['value']
    if low is not None and high is not None and low > high:
        issues.append({'code': 'minimum_exceeds_maximum', 'fields': ['min_bandwidth_mbps', 'max_bandwidth_mbps']})
    window, validity = fields['measurement_window_seconds']['value'], fields['valid_for_seconds']['value']
    if window is not None and validity is not None and window > validity:
        issues.append({'code': 'measurement_window_exceeds_validity', 'fields': ['measurement_window_seconds', 'valid_for_seconds']})
    return fields, issues


def normalize(value):
    spec = contract()
    validate_input(value, spec)
    category = classify(value, spec)
    fields, issues = extract(value, spec)
    missing = [name for name in spec['requiredForPlanning'] if fields[name]['value'] is None]
    if not value.get('subject', {}).get('user_id'):
        missing.append('subject.user_id')
    if category['status'] == 'ambiguous':
        issues.append({'code': 'multiple_business_types_require_separate_requests'})
    blocking = []
    for name, capability in [('rtt_p95_max_ms', 'rtt_guarantee_unvalidated'),
                             ('one_way_p95_max_ms', 'one_way_latency_guarantee_unvalidated'),
                             ('loss_max_percent', 'loss_guarantee_unvalidated')]:
        if fields[name]['value'] is not None:
            blocking.append(capability)
            if fields['measurement_window_seconds']['value'] is None:
                missing.append('measurement_window_seconds')
    if fields['direction']['value'] == 'bidirectional':
        blocking.append('simultaneous_bidirectional_guarantee_unvalidated')
    if fields['deadline_seconds']['value'] is not None:
        blocking.append('completion_deadline_requires_workload_and_E_check')
    minimum = fields['min_bandwidth_mbps']['value']
    if minimum is not None and minimum > spec['referenceCapabilities']['rootCapacityMbps']:
        blocking.append('minimum_exceeds_reference_fixture_root_capacity')
    conflicts = any(i['code'] in ['field_conflict', 'field_invalid', 'minimum_exceeds_maximum', 'measurement_window_exceeds_validity'] for i in issues)
    status = 'conflict' if conflicts else 'abstained' if category['status'] == 'unknown' else 'needs_clarification' if (
        category['status'] == 'ambiguous' or missing or issues) else 'parsed'
    return {'contractVersion': spec['contractVersion'], 'request_id': value['request_id'], 'category': category,
            'subject': {'user_id': value.get('subject', {}).get('user_id'), 'session_id': value.get('subject', {}).get('session_id'),
                        'bindingStatus': 'not_verified_against_live_sessions'},
            'requirements': fields, 'status': status, 'clarificationFields': sorted(set(missing)), 'issues': issues,
            'planningInputComplete': status == 'parsed',
            'capabilityReview': {'mode': spec['referenceCapabilities']['mode'], 'blockingFindings': sorted(blocking),
                'candidateForEPlanning': status == 'parsed' and not blocking,
                'note': 'E must validate live identity, capacity, overhead, policy and measurement; no feasibility assertion',
                'evidenceRefs': spec['referenceCapabilities']['evidence']},
            'ignoredForInference': [k for k in ['flow', 'observations'] if k in value],
            'executionAuthorized': False, 'planGenerated': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = normalize(read_json(args.request))
    result['inputSha256'] = hashlib.sha256(args.request.read_bytes()).hexdigest()
    result['contractSha256LF'] = hashlib.sha256(CONTRACT.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'request_id': result['request_id'], 'status': result['status'],
                      'candidateForEPlanning': result['capabilityReview']['candidateForEPlanning']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
