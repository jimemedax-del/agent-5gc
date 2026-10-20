import copy
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('requirements', ROOT / 'scripts/cnf-requirements.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def request(description='视频播放'):
    return {'request_id': 'test', 'description': description, 'subject': {'user_id': 'ue1'},
            'requested': {'direction': 'downlink', 'min_bandwidth_mbps': 4, 'max_bandwidth_mbps': 8,
                          'priority': 'normal', 'valid_for_seconds': 60, 'measurement_window_seconds': 20}}


class RequirementsTests(unittest.TestCase):
    def test_compositional_task_relations_without_application_labels(self):
        for description, expected in [('我们需要及时问答', 'interactive'),
                                      ('直播教学时回答学生问题', 'interactive'),
                                      ('收看录制的科学节目', 'streaming'),
                                      ('迁移历史数据到存储设备', 'bulk'),
                                      ('归档实验目录', 'bulk')]:
            with self.subTest(description=description):
                result = tool.normalize({'request_id': 'x', 'description': description})
                self.assertEqual(result['category']['value'], expected)
                self.assertFalse(result['capabilityReview']['candidateForEPlanning'])

    def test_active_conversation_vs_recording_object(self):
        for description, expected in [('视频会议正在进行', 'interactive'),
                                      ('视频会议的录像作为视频播放', 'streaming'),
                                      ('上传视频会议的录像供以后查阅', 'bulk')]:
            self.assertEqual(tool.normalize({'request_id': 'x', 'description': description})['category']['value'], expected)

    def test_contrast_keeps_positive_scope_and_original_offsets(self):
        description = '不是视频会议而是文件归档，最低需要 2 Mbps'
        result = tool.normalize({'request_id': 'x', 'description': description})
        self.assertEqual(result['category']['value'], 'bulk')
        self.assertEqual(result['requirements']['min_bandwidth_mbps']['value'], 2)
        for evidence in result['category']['evidence']:
            self.assertEqual(description[evidence['start']:evidence['end']], evidence['text'])

    def test_future_service_duration_and_completion_deadline_are_distinct(self):
        for description, validity, deadline in [('视频播放，接下来 2 分钟', 120, None),
                                               ('后台备份，接下来 2 分钟内传完', None, 120),
                                               ('后台备份，10 分钟内备份完', None, 600)]:
            result = tool.normalize({'request_id': 'x', 'description': description})
            self.assertEqual(result['requirements']['valid_for_seconds']['value'], validity)
            self.assertEqual(result['requirements']['deadline_seconds']['value'], deadline)

    def test_mixed_request_does_not_promote_subtask_direction_or_rate(self):
        result = tool.normalize({'request_id': 'x', 'description': '视频会议，同时上传录像归档，上传最多 3 Mbps'})
        self.assertEqual(result['category']['status'], 'ambiguous')
        for name in ['direction', 'max_bandwidth_mbps']:
            self.assertIsNone(result['requirements'][name]['value'])
        self.assertFalse(result['capabilityReview']['candidateForEPlanning'])

    def test_negative_transfer_is_not_direction_or_business_evidence(self):
        result = tool.normalize({'request_id': 'x', 'description': '不上传文件，视频播放'})
        self.assertEqual(result['category']['value'], 'streaming')
        self.assertIsNone(result['requirements']['direction']['value'])

    def test_three_business_definitions_not_application_word(self):
        for description, expected in [('视频会议', 'interactive'), ('视频播放', 'streaming'), ('视频文件归档', 'bulk')]:
            self.assertEqual(tool.normalize(request(description))['category']['value'], expected)
        self.assertIsNone(tool.normalize(request('视频'))['category']['value'])

    def test_category_does_not_fill_numeric_sla_or_priority(self):
        value = tool.normalize({'request_id': 'x', 'description': '视频会议'})
        for field in tool.FIELDS:
            self.assertIsNone(value['requirements'][field]['value'])
        self.assertEqual(value['status'], 'needs_clarification')

    def test_complete_intent_is_not_an_executable_or_feasible_plan(self):
        value = tool.normalize(request())
        self.assertEqual(value['status'], 'parsed')
        self.assertTrue(value['capabilityReview']['candidateForEPlanning'])
        self.assertFalse(value['executionAuthorized'])
        self.assertFalse(value['planGenerated'])
        self.assertEqual(value['subject']['bindingStatus'], 'not_verified_against_live_sessions')

    def test_ports_and_observations_cannot_determine_category(self):
        value = tool.normalize({'request_id': 'x', 'flow': {'protocol': 'UDP', 'destination_port': 5201},
                                'observations': {'throughput_mbps': 8, 'rtt_p95_ms': 2, 'loss_percent': 0}})
        self.assertEqual(value['status'], 'abstained')
        self.assertTrue(all(v['value'] is None for v in value['requirements'].values()))

    def test_counterfactual_port_swap_preserves_intent(self):
        first, second = request(), request()
        first['flow'] = {'protocol': 'TCP', 'source_port': 55000, 'destination_port': 31301}
        second['flow'] = {'protocol': 'UDP', 'source_port': 1234, 'destination_port': 31404}
        self.assertEqual(tool.normalize(first), tool.normalize(second))

    def test_observations_do_not_become_requested_targets(self):
        value = request('当前实测 RTT p95 不超过 20 ms，视频播放')
        value['observations'] = {'throughput_mbps': 90, 'rtt_p95_ms': 4, 'loss_percent': 10}
        result = tool.normalize(value)
        self.assertIsNone(result['requirements']['rtt_p95_max_ms']['value'])
        self.assertIsNone(result['requirements']['loss_max_percent']['value'])

    def test_explicit_numbers_have_original_text_spans_and_units(self):
        text = '后台备份，上行，最低 500 kbps，最高 1 MB/s，优先级低，有效期 2 分钟，统计窗口 20 秒'
        value = tool.normalize({'request_id': 'x', 'description': text, 'subject': {'user_id': 'ue1'}})
        self.assertEqual(value['requirements']['min_bandwidth_mbps']['value'], .5)
        self.assertEqual(value['requirements']['max_bandwidth_mbps']['value'], 8)
        self.assertEqual(value['requirements']['valid_for_seconds']['value'], 120)
        for field in value['requirements'].values():
            for source in field['sources']:
                self.assertEqual(text[source['start']:source['end']], source['text'])

    def test_no_unqualified_bandwidth_default(self):
        value = tool.normalize({'request_id': 'x', 'description': '视频播放，带宽 6 Mbps'})
        self.assertIsNone(value['requirements']['min_bandwidth_mbps']['value'])
        self.assertIsNone(value['requirements']['max_bandwidth_mbps']['value'])

    def test_unparsed_latency_or_loss_target_cannot_be_silently_dropped(self):
        for phrase in ['RTT < 20 ms', '丢包率小于 1%', '需要低时延', '需要低丢包']:
            value = tool.normalize(request('视频播放，' + phrase))
            self.assertEqual(value['status'], 'needs_clarification')
            self.assertFalse(value['capabilityReview']['candidateForEPlanning'])

    def test_unknown_priority_cannot_be_replaced_by_structured_normal(self):
        value = tool.normalize(request('视频播放，优先级紧急'))
        self.assertEqual(value['status'], 'needs_clarification')
        self.assertFalse(value['capabilityReview']['candidateForEPlanning'])

    def test_unsupported_units_cannot_override_or_hide_existing_targets(self):
        for phrase in ['最低 1 MiB/s', '有效期 1 天', 'deadline 2 days']:
            value = tool.normalize(request('视频播放，' + phrase))
            self.assertEqual(value['status'], 'needs_clarification')
            self.assertFalse(value['capabilityReview']['candidateForEPlanning'])

    def test_text_and_structured_contradiction_is_not_silently_resolved(self):
        value = tool.normalize(request('视频播放，最低 6 Mbps'))
        self.assertEqual(value['status'], 'conflict')
        self.assertIsNone(value['requirements']['min_bandwidth_mbps']['value'])
        self.assertEqual(len(value['requirements']['min_bandwidth_mbps']['sources']), 2)

    def test_negative_phrase_is_not_positive_evidence(self):
        value = tool.normalize(request('不是视频会议，是后台备份'))
        self.assertEqual(value['category']['value'], 'bulk')

    def test_multiple_businesses_need_separate_requests(self):
        value = tool.normalize(request('视频会议和后台备份'))
        self.assertIsNone(value['category']['value'])
        self.assertEqual(value['category']['status'], 'ambiguous')
        self.assertFalse(value['capabilityReview']['candidateForEPlanning'])

    def test_deadline_does_not_invent_priority_or_validity(self):
        value = tool.normalize({'request_id': 'x', 'description': '后台备份，在 30 秒内完成'})
        self.assertEqual(value['requirements']['deadline_seconds']['value'], 30)
        self.assertIsNone(value['requirements']['priority']['value'])
        self.assertIsNone(value['requirements']['valid_for_seconds']['value'])

    def test_context_fact_and_description_conflict_abstains(self):
        value = request('视频播放')
        value['context'] = {'interaction': 'closed_loop'}
        self.assertEqual(tool.normalize(value)['category']['status'], 'ambiguous')

    def test_explicit_no_interaction_conflicts_with_interactive_word(self):
        value = request('视频会议')
        value['context'] = {'interaction': 'none'}
        result = tool.normalize(value)
        self.assertIsNone(result['category']['value'])
        self.assertEqual(result['category']['status'], 'ambiguous')
        self.assertFalse(result['capabilityReview']['candidateForEPlanning'])

    def test_latency_loss_and_bidir_are_not_accepted_guarantees(self):
        for field, item in [('rtt_p95_max_ms', 20), ('one_way_p95_max_ms', 10), ('loss_max_percent', .1), ('direction', 'bidirectional')]:
            value = request()
            value['requested'][field] = item
            result = tool.normalize(value)
            self.assertTrue(result['planningInputComplete'])
            self.assertTrue(result['capabilityReview']['blockingFindings'])
            self.assertFalse(result['capabilityReview']['candidateForEPlanning'])

    def test_conflicting_bounds_and_measurement_windows(self):
        for changes in [{'min_bandwidth_mbps': 9, 'max_bandwidth_mbps': 8}, {'measurement_window_seconds': 90}]:
            value = request()
            value['requested'].update(changes)
            self.assertEqual(tool.normalize(value)['status'], 'conflict')

    def test_no_single_request_claim_of_joint_capacity(self):
        first, second = request(), request('后台备份')
        for value in [first, second]:
            value['requested']['min_bandwidth_mbps'] = 6
            result = tool.normalize(value)
            self.assertTrue(result['capabilityReview']['candidateForEPlanning'])
            self.assertFalse(result['planGenerated'])

    def test_unsupported_or_label_leaking_input_rejected(self):
        for key in ['expected_class', 'target_backend', 'five_qi', 'command']:
            value = request()
            value[key] = 'interactive'
            with self.assertRaises(ValueError):
                tool.normalize(value)

    def test_invalid_nonfinite_boolean_and_zero_targets_rejected(self):
        for number in [True, float('nan'), float('inf'), 0, -1]:
            value = request()
            value['requested']['min_bandwidth_mbps'] = number
            with self.assertRaises(ValueError):
                tool.normalize(value)

    def test_natural_invalid_number_preserved_as_conflict_without_nonfinite_json(self):
        value = tool.normalize({'request_id': 'x', 'description': '视频播放，最低 ' + '9' * 400 + ' Mbps'})
        self.assertEqual(value['status'], 'conflict')
        json.dumps(value, allow_nan=False)

    def test_duplicate_keys_and_nonfinite_json_rejected(self):
        with TemporaryDirectory() as parent:
            path = Path(parent) / 'request.json'
            for text in ['{"request_id":"x","request_id":"y"}', '{"value":NaN}']:
                path.write_text(text)
                with self.assertRaises(ValueError):
                    tool.read_json(path)


if __name__ == '__main__':
    unittest.main()
