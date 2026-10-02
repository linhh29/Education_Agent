"""Restore contract against the real service and a temporary synthetic JSON store.

No model/client constructor, runtime config, credentials, network or demo database.
"""
import copy
import tempfile
import unittest
from pathlib import Path

from companion import CompanionService, JsonStore, ProductError


class MemoryRestoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.service = CompanionService.__new__(CompanionService)
        self.service.store = JsonStore(Path(temp.name) / 'synthetic.json', lambda: {
            name: {} for name in ('profiles', 'messages', 'memoryItems', 'conversations', 'requests', 'parentFeedback')
        })
        self.child = self.service.save_profile({'nickname': '恢复合成资料', 'age': 5})['id']
        self.item = self.service.add_reminder({'childId': self.child, 'summary': '第一版'})

    def current(self, memory_id=None):
        return self.service.store.read()['memoryItems'][memory_id or self.item['id']]

    def change(self, action, **extra):
        return self.service.update_memory(self.item['id'], {
            'childId': self.child, 'action': action,
            'expectedVersion': self.current()['version'], **extra,
        })

    def restore_body(self, request_id='restore_request_1'):
        return {'childId': self.child, 'action': 'restore',
                'expectedVersion': self.current()['version'], 'requestId': request_id}

    def test_single_restore_recovers_latest_contents_after_delete_or_withdraw(self):
        self.change('edit', summary='第二版')
        for action in ('delete', 'withdraw'):
            with self.subTest(action=action):
                self.change(action)
                before = self.current()
                result = self.change('restore', requestId='restore_normal_' + action)
                self.assertEqual(result['summary'], '第二版')
                self.assertEqual(result['status'], 'parent_confirmed')
                self.assertEqual(result['version'], before['version'] + 1)
                self.assertEqual(result['history'][:-1], before['history'])
                self.assertEqual(result['history'][-1]['status'], before['status'])

    def test_two_windows_with_different_request_ids_and_old_version_conflict(self):
        self.change('edit', summary='第二版')
        self.change('delete')
        first = self.restore_body('restore_window_a')
        second = {**first, 'requestId': 'restore_window_b'}
        self.service.update_memory(self.item['id'], first)
        before = self.service.store.read()
        with self.assertRaises(ProductError) as error:
            self.service.update_memory(self.item['id'], second)
        self.assertEqual((error.exception.status, error.exception.code), (409, 'memory_conflict'))
        self.assertEqual(self.service.store.read(), before)
        self.assertEqual(self.current()['summary'], '第二版')

    def test_lost_response_retries_same_request_without_mutation_even_after_later_edit(self):
        self.change('delete')
        body = self.restore_body()
        result = self.service.update_memory(self.item['id'], body)
        before = self.service.store.read()
        replay = self.service.update_memory(self.item['id'], body)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['appliedVersion'], result['version'])
        self.assertEqual(self.service.store.read(), before)
        self.change('edit', summary='恢复后的新编辑')
        after_edit = self.service.store.read()
        replay = self.service.update_memory(self.item['id'], body)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['summary'], '恢复后的新编辑')
        self.assertEqual(replay['appliedVersion'], result['version'])
        self.assertEqual(self.service.store.read(), after_edit)

    def test_same_request_id_cannot_be_reused_for_another_target_or_history(self):
        self.change('delete')
        body = self.restore_body()
        self.service.update_memory(self.item['id'], body)
        other = self.service.add_reminder({'childId': self.child, 'summary': '另一条记录'})
        before = self.service.store.read()
        for memory_id, fields in ((other['id'], body),
                                  (self.item['id'], {**body, 'action': 'restore_version', 'historyIndex': 0})):
            with self.subTest(memory_id=memory_id, action=fields['action']):
                with self.assertRaises(ProductError) as error:
                    self.service.update_memory(memory_id, fields)
                self.assertEqual(error.exception.status, 409)
                self.assertEqual(self.service.store.read(), before)

    def test_reactivation_on_active_record_does_not_restore_earlier_history(self):
        self.change('edit', summary='第二版')
        before = self.service.store.read()
        result = self.change('restore', requestId='restore_active_noop')
        self.assertTrue(result['unchanged'])
        self.assertEqual(result['summary'], '第二版')
        self.assertEqual(self.service.store.read(), before)

    def test_selected_history_is_copied_and_prior_history_is_preserved(self):
        self.change('edit', summary='第二版')
        self.change('edit', summary='第三版')
        before = self.current()
        body = {'childId': self.child, 'action': 'restore_version', 'historyIndex': 0,
                'expectedVersion': before['version'], 'requestId': 'restore_selected_first'}
        restored = self.service.update_memory(self.item['id'], body)
        self.assertEqual(restored['summary'], '第一版')
        self.assertEqual(restored['history'][:-1], before['history'])
        self.assertEqual(restored['history'][-1]['summary'], '第三版')
        self.assertEqual(restored['version'], before['version'] + 1)
        saved = self.service.store.read()
        self.assertTrue(self.service.update_memory(self.item['id'], body)['replayed'])
        self.assertEqual(self.service.store.read(), saved)
        selected_third = self.change('restore_version', historyIndex=2, requestId='restore_selected_third')
        self.assertEqual(selected_third['summary'], '第三版')
        self.assertEqual(selected_third['history'][:len(restored['history'])], restored['history'])

    def test_restoring_deleted_withdrawn_record_preserves_withdrawal(self):
        self.change('edit', summary='第二版')
        self.change('withdraw')
        self.change('delete')
        body = self.restore_body()
        result = self.service.update_memory(self.item['id'], body)
        self.assertEqual(result['status'], 'withdrawn')
        self.assertEqual(result['summary'], '第二版')
        before = self.service.store.read()
        self.assertEqual(self.service.update_memory(self.item['id'], body)['status'], 'withdrawn')
        self.assertEqual(self.service.store.read(), before)
        # A separate, explicit reactivation from the withdrawn detail is still possible.
        self.assertEqual(self.change('restore', requestId='restore_withdrawn_explicit')['status'], 'parent_confirmed')

    def test_version_and_selected_history_are_required_before_any_write(self):
        self.change('delete')
        before = self.service.store.read()
        cases = ({'action': 'restore'},
                 {'action': 'restore_version', 'expectedVersion': self.current()['version']},
                 {'action': 'restore_version', 'expectedVersion': self.current()['version'], 'historyIndex': 999})
        for fields in cases:
            with self.subTest(fields=fields):
                with self.assertRaises(ProductError):
                    self.service.update_memory(self.item['id'], {'childId': self.child, **fields})
                self.assertEqual(self.service.store.read(), before)

    def test_legacy_history_without_version_numbers_is_kept(self):
        self.change('edit', summary='第二版')
        self.change('delete')
        with self.service.store.transaction() as db:
            for entry in db['memoryItems'][self.item['id']]['history']:
                entry.pop('version', None)
                entry.pop('conversationId', None)
        before = copy.deepcopy(self.current()['history'])
        result = self.change('restore', requestId='restore_legacy_history')
        self.assertEqual(result['summary'], '第二版')
        self.assertEqual(result['history'][:len(before)], before)
        previous = self.change('restore_version', historyIndex=0, requestId='restore_legacy_version')
        self.assertEqual(previous['summary'], '第一版')
        self.assertEqual(previous['history'][:len(before)], before)


if __name__ == '__main__':
    unittest.main()
