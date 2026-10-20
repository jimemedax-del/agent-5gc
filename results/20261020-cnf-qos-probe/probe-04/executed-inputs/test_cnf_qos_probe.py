import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = load('qos_probe', 'cnf-qos-probe.py')
auditor = load('qos_auditor', 'audit-cnf-qos-probe.py')


class QoSProbeSafetyTests(unittest.TestCase):
    def setUp(self):
        self.spec = probe.config()
        self.record = {'ueId': probe.SUPI, 'servingPlmnId': '20893', 'singleNssai': {'sst': 1, 'sd': '010203'},
                       'dnnConfigurations': {'internet': {'sessionAmbr': self.spec['originalAmbr'],
                                                         '5gQosProfile': {'5qi': 9}}}}

    def test_write_only_two_rate_fields_with_identity_and_old_rate_guard(self):
        after = copy.deepcopy(self.record)
        after['dnnConfigurations']['internet']['sessionAmbr'] = self.spec['probeAmbr']
        calls = []

        def mongo(code):
            calls.append(code)
            return {'matchedCount': 1, 'modifiedCount': 1}

        with patch.object(probe, 'subscription', side_effect=[self.record, after]), patch.object(probe, 'mongo', mongo):
            probe.set_ambr(self.spec['originalAmbr'], self.spec['probeAmbr'])
        query, update = json.JSONDecoder().raw_decode(calls[0].split('.updateOne(', 1)[1])[0], None
        rest = calls[0].split('.updateOne(', 1)[1]
        _, offset = json.JSONDecoder().raw_decode(rest)
        update, _ = json.JSONDecoder().raw_decode(rest[offset + 1:])
        self.assertEqual(query, {'ueId': probe.SUPI, 'servingPlmnId': '20893',
            probe.FIELDS + '.uplink': '1000 Mbps', probe.FIELDS + '.downlink': '1000 Mbps'})
        self.assertEqual(update, {'$set': {probe.FIELDS + '.uplink': '2 Mbps', probe.FIELDS + '.downlink': '4 Mbps'}})
        self.assertNotIn('authentication', calls[0].lower())

    def test_drift_prevents_write(self):
        self.record['dnnConfigurations']['internet']['sessionAmbr'] = {'uplink': '7 Mbps', 'downlink': '7 Mbps'}
        with patch.object(probe, 'subscription', return_value=self.record), patch.object(probe, 'mongo') as db:
            with self.assertRaisesRegex(RuntimeError, 'drift'):
                probe.set_ambr(self.spec['originalAmbr'], self.spec['probeAmbr'])
            db.assert_not_called()

    def test_unapproved_target_prevents_reads_and_writes(self):
        with patch.object(probe, 'subscription') as read, patch.object(probe, 'mongo') as db:
            with self.assertRaisesRegex(RuntimeError, 'Unapproved'):
                probe.set_ambr(self.spec['originalAmbr'], {'uplink': '20 Mbps', 'downlink': '20 Mbps'})
            read.assert_not_called()
            db.assert_not_called()

    def test_missing_compare_match_is_failure(self):
        with patch.object(probe, 'subscription', return_value=self.record), patch.object(
                probe, 'mongo', return_value={'matchedCount': 0}):
            with self.assertRaisesRegex(RuntimeError, 'compare-and-set'):
                probe.set_ambr(self.spec['originalAmbr'], self.spec['probeAmbr'])

    def test_restore_disables_policer_before_attempting_session_renewal(self):
        calls = []
        with patch.object(probe, 'subscription', return_value=self.record), patch.object(
                probe, 'switch', side_effect=lambda x: calls.append(('switch', x))), patch.object(
                probe, 'set_ambr', side_effect=lambda *x: calls.append(('policy', x))), patch.object(
                probe.m1, 'save'), patch.object(probe, 'renew', side_effect=RuntimeError('failed renewal')):
            with self.assertRaisesRegex(RuntimeError, 'failed renewal'):
                probe.restore(Path('unused'))
        self.assertEqual(calls[0], ('switch', 0))
        self.assertEqual(calls[1][0], 'policy')

    def test_session_recovery_waits_for_auto_session_without_manual_duplicate(self):
        old = 'PDU Session1:\n state: PS-ACTIVE\n address: 10.60.0.7\nPDU Session2:\n state: PS-ACTIVE\n address: 10.60.0.8\n'
        fresh = 'PDU Session1:\n state: PS-ACTIVE\n address: 10.60.0.9\n'
        with patch.object(probe, 'cli', side_effect=[old, 'release requested', fresh]) as cli, patch.object(
                probe.m1, 'save'), patch.object(probe.m1, 'kube', return_value=SimpleNamespace(
                    returncode=0, stdout='[{"addr_info":[{"local":"10.60.0.9"}]}]')):
            probe.renew(Path('unused'), 'recover')
        self.assertEqual([c.args[0] for c in cli.call_args_list], ['ps-list', 'ps-release 1 2', 'ps-list'])

    def test_unknown_session_is_not_released(self):
        with patch.object(probe, 'cli', return_value='PDU Session9:\n') as cli:
            with self.assertRaisesRegex(RuntimeError, 'session set'):
                probe.renew(Path('unused'), 'recover')
        self.assertEqual(cli.call_count, 1)


class QoSAuditRejectionTests(unittest.TestCase):
    def rates(self):
        return [{'epoch': epoch, 'direction': direction, 'receivedMbps': value}
                for epoch in ['cap-off', 'cap-on', 'cap-reoff']
                for direction, cap in [('uplink', 2), ('downlink', 4)]
                for value in ([cap * .75] * 3 if epoch == 'cap-on' else [7.9] * 3)]

    def test_reversible_ceiling_is_accepted(self):
        self.assertEqual(auditor.rate_comparison(self.rates())['uplink']['capMbps'], 2)

    def test_unchanged_data_plane_does_not_claim_upf_effect(self):
        rows = self.rates()
        for row in rows:
            row['receivedMbps'] = 7.9
        self.assertEqual(auditor.rate_comparison(rows)['uplink']['classification'], 'no-clean-causal-conclusion')

    def test_low_off_control_does_not_claim_upf_effect(self):
        rows = self.rates()
        rows[-1]['receivedMbps'] = .5
        self.assertNotEqual(auditor.rate_comparison(rows)['downlink']['classification'], 'upf-switch-effect-isolated')

    def test_cap_with_switch_off_does_not_claim_upf_effect(self):
        rows = self.rates()
        for row in rows:
            row['receivedMbps'] = 1.8 if row['direction'] == 'uplink' else 3.7
        result = auditor.rate_comparison(rows)
        self.assertEqual(result['uplink']['classification'],
                         'ceiling-present-with-switch-off; upf-contribution-not-isolated')

    def test_missing_round_rejected(self):
        with self.assertRaisesRegex(ValueError, 'rounds'):
            auditor.rate_comparison(self.rates()[:-1])

    def test_complete_outage_not_a_valid_cap(self):
        rows = self.rates()
        for row in rows:
            if row['epoch'] == 'cap-on':
                row['receivedMbps'] = 0
        with self.assertRaisesRegex(ValueError, 'Outage'):
            auditor.rate_comparison(rows)

    def test_qfi_does_not_bind_a_rule_from_another_session(self):
        raw = {'pdr': [
            {'ID': 1, 'SEID': 10, 'PDI': {'UEAddr': '10.60.0.4', 'FTEID': {'TEID': 4}}, 'QERID': [3, 4]},
            {'ID': 2, 'SEID': 10, 'PDI': {'UEAddr': '10.60.0.4', 'FTEID': None}, 'QERID': [3, 4]}],
            'qer': [{'ID': qid, 'SEID': 11, 'QFI': 1, 'Gate': 0} for qid in [3, 4]]}
        with self.assertRaisesRegex(ValueError, 'QFI/gate'):
            auditor.binding(raw, '10.60.0.4', 2000, 4000)

    def test_complete_timed_payload_reset_is_recorded(self):
        entries = [{'flags': '-', 'bytes_received': 500, 'duration_ms': 15100},
                   {'flags': 'UR', 'bytes_received': 4000000, 'duration_ms': 15050}]
        self.assertEqual(auditor.proxy_status(entries, 3800000, 'uplink'), ['-', 'UR'])

    def test_premature_payload_reset_is_not_a_complete_measurement(self):
        entries = [{'flags': '-', 'bytes_received': 500, 'duration_ms': 15100},
                   {'flags': 'UR', 'bytes_received': 4000000, 'duration_ms': 5000}]
        with self.assertRaisesRegex(ValueError, 'early'):
            auditor.proxy_status(entries, 3800000, 'uplink')

    def test_upstream_connection_failure_is_rejected(self):
        entries = [{'flags': '-', 'bytes_received': 500, 'duration_ms': 15100},
                   {'flags': 'UF', 'bytes_received': 4000000, 'duration_ms': 15050}]
        with self.assertRaisesRegex(ValueError, 'proxy failure'):
            auditor.proxy_status(entries, 3800000, 'uplink')


if __name__ == '__main__':
    unittest.main()
