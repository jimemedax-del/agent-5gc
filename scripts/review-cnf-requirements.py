#!/usr/bin/env python3
"""D2 offline human annotation intake, integrity audit and frozen-baseline evaluation."""
import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parent.parent
D1_CASES = ROOT / 'tests/fixtures/requirements-cases.json'
BASELINE_FILES = ('scripts/cnf-requirements.py', 'infra/cnfs/requirements-contract.json')
CATEGORIES = ('interactive', 'streaming', 'bulk')


def load_baseline(root):
    spec = importlib.util.spec_from_file_location('d2_frozen_requirements', root / BASELINE_FILES[0])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


baseline = load_baseline(ROOT)
require = baseline.require
read_json = baseline.read_json


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def file_hash(path):
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def write_json(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def text(value, name):
    require(isinstance(value, str) and bool(value.strip()), name + ' required')


def timestamp(value):
    text(value, 'timestamp')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(parsed.tzinfo is not None, 'Timestamp must include timezone')
    return parsed


def description_key(value):
    return re.sub(r'\s+', '', value.get('description', '')).casefold()


def development_reference():
    dataset = read_json(D1_CASES)
    return {'sourceSha256LF': file_hash(D1_CASES),
            'scenarioFamilies': sorted({c['scenarioFamily'] for c in dataset['cases']}),
            'descriptionKeys': sorted({description_key(c['input']) for c in dataset['cases']})}


def from_d1():
    dataset = read_json(D1_CASES)
    return {'collectionVersion': 'cnf-requirements-collection-v1', 'collectionId': 'd1-synthetic-review',
            'cases': [{'id': c['id'], 'scenarioFamily': c['scenarioFamily'], 'split': 'development',
                       'origin': {'kind': 'synthetic', 'authorKind': 'agent', 'authorId': 'D1-Agent',
                                  'sourceRef': D1_CASES.relative_to(ROOT).as_posix(),
                                  'collectedAt': '2026-10-20T00:00:00+08:00'},
                       'input': copy.deepcopy(c['input'])} for c in dataset['cases']]}


def validate_collection(collection, reference, normalizer):
    require(type(collection) is dict and set(collection) == {'collectionVersion', 'collectionId', 'cases'},
            'Wrong collection keys')
    require(collection['collectionVersion'] == 'cnf-requirements-collection-v1', 'Unknown collection version')
    text(collection['collectionId'], 'collectionId')
    cases = collection['cases']
    require(type(cases) is list and cases, 'Collection must contain original scenarios; empty intake is not data')
    seen, families, descriptions = set(), {}, {}
    for case in cases:
        require(type(case) is dict and set(case) == {'id', 'scenarioFamily', 'split', 'origin', 'input'},
                'Wrong case keys; annotations and predicted labels must not be in collection')
        text(case['id'], 'id')
        require(case['id'] not in seen, 'Duplicate case id')
        seen.add(case['id'])
        text(case['scenarioFamily'], 'scenarioFamily')
        require(case['split'] in ['development', 'holdout'], 'Unknown split')
        families.setdefault(case['scenarioFamily'], set()).add(case['split'])
        normalizer.validate_input(case['input'], normalizer.contract())
        require(case['input']['request_id'] == case['id'], 'Request id differs from case id')
        key = description_key(case['input'])
        if key:
            descriptions.setdefault(key, set()).add(case['split'])
        origin = case['origin']
        require(type(origin) is dict and set(origin) == {'kind', 'authorKind', 'authorId', 'sourceRef', 'collectedAt'},
                'Wrong provenance keys')
        require(origin['kind'] in ['synthetic', 'real_requirement'], 'Unknown origin kind')
        require(origin['authorKind'] in ['human', 'agent'], 'Unknown author kind')
        text(origin['authorId'], 'authorId')
        text(origin['sourceRef'], 'sourceRef')
        timestamp(origin['collectedAt'])
        if origin['kind'] == 'real_requirement':
            require(origin['authorKind'] == 'human', 'Agent-generated scenarios are not real human requirements')
        if case['split'] == 'holdout':
            require(origin['kind'] == 'real_requirement', 'Synthetic scenarios cannot become real holdout data')
            require(case['scenarioFamily'] not in reference['scenarioFamilies'], 'D1 scenario family reused in holdout')
            require(not key or key not in reference['descriptionKeys'], 'D1 description reused in holdout')
    require(all(len(v) == 1 for v in families.values()), 'Scenario family crosses splits')
    require(all(len(v) == 1 for v in descriptions.values()), 'Duplicate description crosses splits')


def immutable_review(packet):
    require(set(packet) == {'reviewVersion', 'collectionId', 'instructions', 'categoryDefinitions', 'items'},
            'Wrong review packet keys')
    return {**packet, 'items': [{k: v for k, v in item.items() if k not in ['review', 'targetReview']}
                               for item in packet['items']]}


def render_review(packet):
    """A local, category-only form; numeric labels remain independently unreviewed."""
    embedded = json.dumps(packet, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c').replace('&', '\\u0026')
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>D2 业务需求人工标注</title>
<style>body{font:16px/1.6 system-ui,sans-serif;max-width:960px;margin:32px auto;padding:0 20px;color:#182536;background:#f5f7fa}
article,header{padding:22px;background:white;border:1px solid #d6dde6;border-radius:10px;margin:18px 0}h1{font-size:26px}
label{display:block;margin:12px 0}select,input,textarea,button{font:inherit;padding:8px;max-width:100%;box-sizing:border-box}
textarea,select{width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px}button{background:#164a72;color:white;border:0;border-radius:6px;cursor:pointer}
.note{color:#536173}#status{font-weight:bold}</style><header><h1>D2 业务需求人工标注</h1>
<p>请根据原始任务判断类别。这里不显示识别器预测或草案答案，所有选项初始为空。合成场景审核不能替代真实业务验证。</p>
<div id="definitions"></div><p class="note">本表只标注类别；带宽、时延等目标保持未审核，不会自动填入任何 SLA。所有数据仅在本页面处理。</p>
<label>审核者标识 <input id="reviewer" autocomplete="off" placeholder="姓名或稳定代号"></label>
<label><input id="human" type="checkbox"> 我本人完成了这些判断</label>
<label><input id="independent" type="checkbox"> 我在标注这些样本前没有看过其识别器预测</label>
<p class="note">以上为人工声明，工具不能核实身份或盲标过程。若已看过样本预测，请另请审核者，勿勾选独立声明。</p>
</header><main id="items"></main><button id="export">导出当前标注 JSON</button><p id="status" role="status"></p>
<p>将导出的文件保存到本审核包目录中的 review.json，再运行审核和评估。可分次保存；类别未全部标注时不会产生正式分数。导出文件不代表审批网络策略。</p>
<script type="application/json" id="data">''' + embedded + '''</script><script>
const packet=JSON.parse(document.getElementById('data').textContent), controls=[];
const names={interactive:'实时交互',streaming:'持续媒体',bulk:'后台传输',unknown:'未知',ambiguous:'混合或歧义'};
for(const c of packet.categoryDefinitions){const p=document.createElement('p');p.textContent=c.label+'：'+c.definition;document.getElementById('definitions').append(p);}
for(const [index,item] of packet.items.entries()){
 const card=document.createElement('article'), title=document.createElement('h2'), raw=document.createElement('pre');
 title.textContent=(index+1)+'. '+item.id;raw.textContent=JSON.stringify({scenarioFamily:item.scenarioFamily,origin:item.origin,input:item.input},null,2);
 const label=document.createElement('label');label.textContent='人工类别';const select=document.createElement('select');
 for(const [value,name] of [['','尚未标注'],...Object.entries(names)]){const option=document.createElement('option');option.value=value;option.textContent=name;select.append(option);}
 select.value=item.review.decision==='known'?item.review.category:item.review.decision==='unreviewed'?'':item.review.decision;
 const why=document.createElement('label');why.textContent='判断依据（引用业务事实，不依据端口或后端）';const reason=document.createElement('textarea');reason.rows=2;reason.value=item.review.reason;
 label.append(select);why.append(reason);card.append(title,raw,label,why);document.getElementById('items').append(card);controls.push({item,select,reason});
}
document.getElementById('export').onclick=()=>{
 const selected=controls.filter(c=>c.select.value), reviewer=document.getElementById('reviewer').value.trim(), status=document.getElementById('status');
 if(selected.length&&(!reviewer||!document.getElementById('human').checked||!document.getElementById('independent').checked)){status.textContent='请填写审核者并确认人工标注及独立声明。';return;}
 if(selected.some(c=>!c.reason.value.trim())){status.textContent='每个已选类别都需要判断依据。';return;}
 if(selected.some(c=>c.item.split==='holdout'&&c.item.origin.authorId===reviewer)){status.textContent='留出样本需要由原始需求作者之外的审核者标注。';return;}
 const output=JSON.parse(JSON.stringify(packet));
 controls.forEach((c,index)=>{const v=c.select.value;output.items[index].review=v?{decision:['unknown','ambiguous'].includes(v)?v:'known',category:['unknown','ambiguous'].includes(v)?null:v,reason:c.reason.value.trim(),reviewerId:reviewer,reviewerKind:'human',reviewedAt:new Date().toISOString(),independentOfPredictions:true}:{decision:'unreviewed',category:null,reason:'',reviewerId:'',reviewerKind:null,reviewedAt:null,independentOfPredictions:false};});
 const url=URL.createObjectURL(new Blob([JSON.stringify(output,null,2)+'\\n'],{type:'application/json;charset=utf-8'})),a=document.createElement('a');a.href=url;a.download='review.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);status.textContent='已导出 '+selected.length+' / '+controls.length+' 条类别标注；数值目标审核状态保持原样。';
};</script></html>'''


def prepare(collection, out):
    reference = development_reference()
    validate_collection(collection, reference, baseline)
    prepared_at = datetime.now(timezone.utc)
    require(all(timestamp(c['origin']['collectedAt']) <= prepared_at for c in collection['cases']),
            'Collection timestamps cannot be in the future')
    packet = {'reviewVersion': 'cnf-requirements-review-v2', 'collectionId': collection['collectionId'],
              'instructions': {'category': 'Record known/category, unknown, or ambiguous; explain using original business facts.',
                               'targets': 'Only explicitly requested targets; no default SLA. Null means unknown/conflicting/invalid, not zero.',
                               'independence': 'Human reviewer declares no recognizer predictions seen before labeling; this declaration is not authenticated.',
                               'evidence': 'Every reviewed target needs a basis. Explicit/conflict/invalid targets also need original evidence: path and quote or value.',
                               'scope': 'Real requirement text is not evidence of an executed network workload or an SLA guarantee.'},
              'categoryDefinitions': [{k: c[k] for k in ['id', 'label', 'definition']}
                                      for c in baseline.contract()['categories']],
              'items': [{**copy.deepcopy(c),
                         'review': {'decision': 'unreviewed', 'category': None, 'reason': '', 'reviewerId': '',
                                    'reviewerKind': None, 'reviewedAt': None, 'independentOfPredictions': False},
                         'targetReview': {'status': 'unreviewed', 'fields': {}}} for c in collection['cases']]}
    out.mkdir(parents=True, exist_ok=False)
    frozen = out / 'frozen-inputs'
    hashes = {}
    for relative in [*BASELINE_FILES, 'scripts/review-cnf-requirements.py']:
        target = frozen / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
        hashes[relative] = file_hash(target)
    write_json(out / 'collection.json', collection)
    write_json(out / 'development-reference.json', reference)
    write_json(out / 'review.json', packet)
    manifest = {'bundleVersion': 'cnf-requirements-review-bundle-v1', 'route': 'R2', 'stage': 'R2.S4', 'task': 'D2',
                'preparedAt': prepared_at.isoformat(), 'baselineSha256LF': hashes,
                'collectionSha256LF': file_hash(out / 'collection.json'),
                'developmentReferenceSha256LF': file_hash(out / 'development-reference.json'),
                'immutableReviewSha256': digest(immutable_review(packet)),
                'provenanceAuthentication': 'declarations_only_not_external_identity_verification',
                'clusterAccessed': False}
    write_json(out / 'manifest.json', manifest)
    (out / 'review.html').write_text(render_review(packet), encoding='utf-8')
    return packet


def validate_evidence(evidence, request):
    require(type(evidence) is dict and evidence.get('path') in ['/description', *['/requested/' + f for f in baseline.FIELDS]],
            'Evidence must point to original description or explicit requested field')
    if evidence['path'] == '/description':
        require(set(evidence) == {'path', 'quote'}, 'Wrong text evidence keys')
        text(evidence['quote'], 'evidence quote')
        require(evidence['quote'] in request.get('description', ''), 'Quote absent from original description')
    else:
        require(set(evidence) == {'path', 'value'}, 'Wrong structured evidence keys')
        field = evidence['path'].split('/')[-1]
        require(field in request.get('requested', {}) and
                type(evidence['value']) is type(request['requested'][field]) and
                evidence['value'] == request['requested'][field], 'Structured evidence differs from original input')


def validate_labels(item, normalizer):
    require(set(item) == {'id', 'scenarioFamily', 'split', 'origin', 'input', 'review', 'targetReview'}, 'Wrong item keys')
    review = item['review']
    require(type(review) is dict and set(review) == {'decision', 'category', 'reason', 'reviewerId', 'reviewerKind',
                                                  'reviewedAt', 'independentOfPredictions'}, 'Wrong category review keys')
    decision = review['decision']
    require(decision in ['unreviewed', 'known', 'unknown', 'ambiguous'], 'Unknown review decision')
    if decision == 'unreviewed':
        require(review['category'] is None, 'Unreviewed category cannot contain a label')
    else:
        require((decision == 'known' and review['category'] in CATEGORIES) or
                (decision != 'known' and review['category'] is None), 'Decision and category disagree')
        require(review['reviewerKind'] == 'human' and review['independentOfPredictions'] is True,
                'Human annotation and prediction independence must be explicitly declared')
        text(review['reviewerId'], 'reviewerId')
        text(review['reason'], 'review reason')
        reviewed = timestamp(review['reviewedAt'])
        require(reviewed >= timestamp(item['origin']['collectedAt']), 'Review predates original collection')
        if item['split'] == 'holdout':
            require(review['reviewerId'] != item['origin']['authorId'], 'Holdout author cannot serve as independent reviewer')
    targets = item['targetReview']
    require(type(targets) is dict and set(targets) == {'status', 'fields'}, 'Wrong target review keys')
    require(type(targets['fields']) is dict, 'Target fields must be an object')
    if targets['status'] == 'unreviewed':
        require(not targets['fields'], 'Unreviewed target fields must be empty')
        return
    require(targets['status'] == 'complete' and decision != 'unreviewed', 'Target review needs human category review')
    require(set(targets['fields']) == set(normalizer.FIELDS), 'Complete review must cover every target including unknowns')
    for field, annotation in targets['fields'].items():
        require(type(annotation) is dict and set(annotation) == {'value', 'resolution', 'basis', 'evidence'}, 'Wrong target annotation keys')
        resolution = annotation['resolution']
        require(resolution in ['explicit', 'unknown', 'conflict', 'invalid'], 'Unknown field resolution')
        text(annotation['basis'], 'target basis')
        require(type(annotation['evidence']) is list, 'Evidence must be a list')
        if resolution == 'explicit':
            normalizer.validate_value(field, annotation['value'], normalizer.contract())
        else:
            require(annotation['value'] is None, 'Non-explicit target must remain null')
        require(resolution == 'unknown' or annotation['evidence'], 'Explicit/conflicting/invalid targets require evidence')
        for evidence in annotation['evidence']:
            validate_evidence(evidence, item['input'])


def load_bundle(review_path):
    folder = review_path.parent
    manifest = read_json(folder / 'manifest.json')
    require(manifest['bundleVersion'] == 'cnf-requirements-review-bundle-v1', 'Unknown bundle version')
    expected_files = {*BASELINE_FILES, 'scripts/review-cnf-requirements.py'}
    require(set(manifest['baselineSha256LF']) == expected_files, 'Unknown frozen file set')
    require(file_hash(Path(__file__)) == manifest['baselineSha256LF']['scripts/review-cnf-requirements.py'],
            'Review tool changed; use the archived frozen-inputs/scripts/review-cnf-requirements.py')
    for relative, expected in manifest['baselineSha256LF'].items():
        require(file_hash(folder / 'frozen-inputs' / relative) == expected, 'Frozen source hash mismatch: ' + relative)
    require(file_hash(folder / 'collection.json') == manifest['collectionSha256LF'], 'Original collection changed')
    require(file_hash(folder / 'development-reference.json') == manifest['developmentReferenceSha256LF'], 'Development reference changed')
    packet = read_json(review_path)
    require(packet['reviewVersion'] == 'cnf-requirements-review-v2', 'Unknown review version')
    require(digest(immutable_review(packet)) == manifest['immutableReviewSha256'], 'Raw facts, split or membership changed after freezing')
    collection = read_json(folder / 'collection.json')
    normalizer = load_baseline(folder / 'frozen-inputs')
    validate_collection(collection, read_json(folder / 'development-reference.json'), normalizer)
    require([{k: v for k, v in i.items() if k not in ['review', 'targetReview']} for i in packet['items']] == collection['cases'],
            'Review does not match original collection')
    for item in packet['items']:
        validate_labels(item, normalizer)
        if item['review']['decision'] != 'unreviewed':
            require(timestamp(item['review']['reviewedAt']) >= timestamp(manifest['preparedAt']),
                    'Review predates frozen annotation packet')
    return packet, manifest, normalizer


def audit(packet, manifest):
    items = packet['items']
    reviewed = [i for i in items if i['review']['decision'] != 'unreviewed']
    targets = [i for i in items if i['targetReview']['status'] == 'complete']
    categories = Counter(i['review']['category'] for i in reviewed if i['review']['decision'] == 'known')
    return {'auditVersion': 'cnf-requirements-review-audit-v1', 'route': 'R2', 'stage': 'R2.S4', 'task': 'D2',
            'integrity': 'passed', 'cases': len(items), 'humanCategoryDeclarations': len(reviewed),
            'pendingCategories': len(items) - len(reviewed), 'completeTargetReviews': len(targets),
            'pendingTargetReviews': len(items) - len(targets),
            'originCounts': dict(Counter(i['origin']['kind'] for i in items)),
            'splitCounts': dict(Counter(i['split'] for i in items)), 'reviewedCategoryCounts': dict(categories),
            'reviewedUnknown': sum(i['review']['decision'] == 'unknown' for i in reviewed),
            'reviewedAmbiguous': sum(i['review']['decision'] == 'ambiguous' for i in reviewed),
            'missingReviewedCategories': [c for c in CATEGORIES if not categories[c]],
            'evaluationReady': len(reviewed) == len(items),
            'frozenBaselineSha256LF': manifest['baselineSha256LF'],
            'reviewProvenance': 'human declarations, not independently authenticated; blindness not technically provable',
            'leakageCheckScope': 'declared family identifiers and whitespace-normalized exact descriptions; semantic paraphrase leakage requires human review',
            'realWorkloadExecutionVerified': False, 'stageDAccepted': False, 'clusterAccessed': False}


def evaluate(packet, manifest, normalizer):
    readiness = audit(packet, manifest)
    require(readiness['evaluationReady'], 'Category annotations incomplete; use audit, not a partial scored subset')
    records = []
    for item in packet['items']:
        # Raw business facts only. Neither human annotations nor origin/split metadata reach the recognizer.
        prediction = normalizer.normalize(copy.deepcopy(item['input']))
        review, target = item['review'], item['targetReview']
        predicted = prediction['category']
        decision_match = (predicted['value'] == review['category'] if review['decision'] == 'known'
                          else predicted['status'] == review['decision'])
        comparisons = []
        if target['status'] == 'complete':
            for field, expected in target['fields'].items():
                actual = prediction['requirements'][field]
                comparisons.append({'field': field, 'expectedValue': expected['value'], 'actualValue': actual['value'],
                                    'expectedResolution': expected['resolution'], 'actualResolution': actual['resolution'],
                                    'evidenceChannels': sorted({'text' if e['path'] == '/description' else 'structured'
                                                                for e in expected['evidence']}),
                                    'valueMatches': actual['value'] == expected['value'],
                                    'resolutionMatches': actual['resolution'] == expected['resolution']})
        records.append({'id': item['id'], 'scenarioFamily': item['scenarioFamily'], 'split': item['split'],
                        'inputMode': 'with_application_context' if any(v != 'unknown' for v in item['input'].get('context', {}).values()) else 'description_only',
                        'originKind': item['origin']['kind'], 'review': review, 'prediction': prediction,
                        'categoryDecisionMatches': decision_match, 'targetComparisons': comparisons})

    def classification_metrics(rows):
        known = [r for r in rows if r['review']['decision'] == 'known']
        other = [r for r in rows if r['review']['decision'] != 'known']
        emitted = [r for r in known if r['prediction']['category']['value'] is not None]
        correct = sum(r['categoryDecisionMatches'] for r in known)
        confusion = Counter((r['review']['category'] or r['review']['decision'],
                             r['prediction']['category']['value'] or r['prediction']['category']['status']) for r in rows)
        return {'cases': len(rows), 'knownCases': len(known), 'knownCorrect': correct,
                'knownAccuracyIncludingRefusals': correct / len(known) if known else None,
                'knownCoverage': len(emitted) / len(known) if known else None,
                'selectiveAccuracy': correct / len(emitted) if emitted else None,
                'unknownOrAmbiguousCases': len(other),
                'safeAbstentions': sum(r['prediction']['category']['value'] is None for r in other),
                'exactDecisionMatches': sum(r['categoryDecisionMatches'] for r in rows),
                'confusion': [{'humanLabel': k[0], 'prediction': k[1], 'cases': n} for k, n in sorted(confusion.items())]}

    def metrics(rows):
        fields = [f for r in rows for f in r['targetComparisons']]
        specified = [f for f in fields if f['expectedResolution'] == 'explicit']
        sources = {'text_only': ['text'], 'structured_only': ['structured'], 'mixed': ['structured', 'text']}
        return {**classification_metrics(rows), 'scenarioFamilies': len({r['scenarioFamily'] for r in rows}),
                'categoryByInputMode': {mode: classification_metrics([r for r in rows if r['inputMode'] == mode])
                                        for mode in sorted({r['inputMode'] for r in rows})},
                'reviewedTargetFields': len(fields), 'explicitTargetFields': len(specified),
                'explicitValueMatches': sum(f['valueMatches'] for f in specified),
                'fieldValueAndResolutionMatches': sum(f['valueMatches'] and f['resolutionMatches'] for f in fields),
                'hallucinatedFields': sum(f['expectedResolution'] != 'explicit' and f['actualValue'] is not None for f in fields),
                'explicitTargetsByEvidence': {name: {'fields': sum(f['evidenceChannels'] == channels for f in specified),
                                                     'valueMatches': sum(f['evidenceChannels'] == channels and f['valueMatches'] for f in specified)}
                                              for name, channels in sources.items()}}

    report = {'evaluationVersion': 'cnf-requirements-reviewed-evaluation-v1', 'route': 'R2', 'stage': 'R2.S4', 'task': 'D2',
              'claimScope': 'Frozen rule baseline against declared human annotations; strata kept separate; no network SLA or stage acceptance',
              'audit': readiness, 'strata': {origin + '/' + split: metrics([r for r in records if r['originKind'] == origin and r['split'] == split])
                                           for origin, split in sorted({(r['originKind'], r['split']) for r in records})},
              'disagreements': [r['id'] for r in records if not r['categoryDecisionMatches'] or
                                any(not (f['valueMatches'] and f['resolutionMatches']) for f in r['targetComparisons'])],
              'unreviewedTargetsNotScored': readiness['pendingTargetReviews'],
              'plansGenerated': sum(r['prediction']['planGenerated'] for r in records),
              'executionsAuthorized': sum(r['prediction']['executionAuthorized'] for r in records),
              'stageDAccepted': False, 'clusterAccessed': False}
    return report, records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prepare_parser = sub.add_parser('prepare')
    source = prepare_parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--d1', action='store_true')
    source.add_argument('--collection', type=Path)
    prepare_parser.add_argument('--out', type=Path, required=True)
    for command in ['audit', 'evaluate', 'render']:
        child = sub.add_parser(command)
        child.add_argument('--review', type=Path, required=True)
        child.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        packet = prepare(from_d1() if args.d1 else read_json(args.collection), args.out)
        print(json.dumps({'prepared': len(packet['items']), 'humanLabelsCreated': 0, 'path': str(args.out / 'review.json')}))
        return
    packet, manifest, normalizer = load_bundle(args.review)
    if args.command == 'render':
        with args.out.open('x', encoding='utf-8') as stream:
            stream.write(render_review(packet))
        print(json.dumps({'outcome': 'rendered', 'humanLabelsCreated': 0}))
        return
    if args.command == 'audit':
        report = audit(packet, manifest)
        write_json(args.out, report)
    else:
        report, records = evaluate(packet, manifest, normalizer)
        args.out.mkdir(parents=True, exist_ok=False)
        for name in ['manifest.json', 'collection.json', 'development-reference.json']:
            shutil.copyfile(args.review.parent / name, args.out / name)
        shutil.copytree(args.review.parent / 'frozen-inputs', args.out / 'frozen-inputs')
        write_json(args.out / 'review.json', packet)
        write_json(args.out / 'report.json', report)
        write_json(args.out / 'predictions.json', records)
        write_json(args.out / 'evaluation-inputs.json', {'reviewSha256LF': file_hash(args.out / 'review.json'),
                   'evaluatorSha256LF': file_hash(Path(__file__)), 'evaluatedAt': datetime.now(timezone.utc).isoformat()})
    print(json.dumps({'outcome': 'audited' if args.command == 'audit' else 'evaluated', 'stageDAccepted': False}))


if __name__ == '__main__':
    main()
