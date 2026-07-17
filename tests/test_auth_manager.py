#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import tempfile
import unittest
from pathlib import Path

from auth_manager import (
    AuthenticationDataError,
    AuthenticationError,
    CredentialStore,
)


class CredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "security" / "credentials.json"
        self.store = CredentialStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_create_and_authenticate_without_plaintext_password(self):
        self.assertFalse(self.store.is_configured())
        session = self.store.create_account("一人公司", "Finance2026!")
        self.assertEqual(session.username, "一人公司")
        self.assertTrue(self.store.is_configured())
        payload = self.path.read_text(encoding="utf-8")
        self.assertNotIn("Finance2026!", payload)
        self.assertEqual(self.store.authenticate("一人公司", "Finance2026!").username, "一人公司")
        self.assertEqual(self.store.authenticate("一人公司", "错误密码"), None)
        self.assertEqual(self.store.authenticate("其他账号", "Finance2026!"), None)
        self.assertFalse(self.path.with_suffix(".json.tmp").exists())

    def test_username_is_case_insensitive_but_preserves_display_form(self):
        self.store.create_account("Owner.Admin", "Ledger2026!")
        session = self.store.authenticate("owner.admin", "Ledger2026!")
        self.assertEqual(session.username, "Owner.Admin")

    def test_change_password_requires_current_password(self):
        self.store.create_account("owner", "Ledger2026!")
        with self.assertRaisesRegex(AuthenticationError, "当前密码"):
            self.store.change_password("owner", "wrong-password", "NewLedger2027!")
        self.store.change_password("owner", "Ledger2026!", "NewLedger2027!")
        self.assertIsNone(self.store.authenticate("owner", "Ledger2026!"))
        self.assertIsNotNone(self.store.authenticate("owner", "NewLedger2027!"))

    def test_rejects_duplicate_account_and_weak_password(self):
        with self.assertRaises(AuthenticationError):
            self.store.create_account("x", "12345678")
        with self.assertRaises(AuthenticationError):
            self.store.create_account("owner", "12345678")
        self.store.create_account("owner", "Ledger2026!")
        with self.assertRaisesRegex(AuthenticationError, "已经创建"):
            self.store.create_account("other", "Other2026!")

    def test_corrupted_existing_record_is_not_treated_as_first_run(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"username": "owner"}), encoding="utf-8")
        with self.assertRaises(AuthenticationDataError):
            self.store.is_configured()
        with self.assertRaises(AuthenticationDataError):
            self.store.authenticate("owner", "Ledger2026!")


if __name__ == "__main__":
    unittest.main()
