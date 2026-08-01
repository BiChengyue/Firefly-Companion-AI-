import os
import unittest
import tempfile
import time
from pathlib import Path
import app.core.db as db

class TestDatabase(unittest.TestCase):
    def setUp(self):
        # Create a temporary database file
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.db_fd)
        
    def tearDown(self):
        # Clean up the thread-local connection before deleting the file
        key = f"conn_{self.db_path}"
        conn = getattr(db._local, key, None)
        if conn:
            conn.close()
            delattr(db._local, key)
            
        # Also clean up the default connection just in case it was used
        default_key = f"conn_{db._DEFAULT_DB_PATH}"
        default_conn = getattr(db._local, default_key, None)
        if default_conn:
            default_conn.close()
            delattr(db._local, default_key)
            
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_session_crud(self):
        session_id = "test-session-1"
        
        # 1. Create session
        session = db.create_session(session_id, title="Test Session", mode="daily", db_path=self.db_path)
        self.assertEqual(session["id"], session_id)
        self.assertEqual(session["title"], "Test Session")
        self.assertEqual(session["mode"], "daily")
        
        # 2. Get session
        retrieved = db.get_session(session_id, db_path=self.db_path)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["id"], session_id)
        self.assertEqual(retrieved["title"], "Test Session")
        
        # 3. Update session title
        success = db.update_session_title(session_id, "Updated Title", db_path=self.db_path)
        self.assertTrue(success)
        retrieved = db.get_session(session_id, db_path=self.db_path)
        self.assertEqual(retrieved["title"], "Updated Title")
        
        # 4. List sessions
        sessions = db.list_sessions(db_path=self.db_path)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["id"], session_id)
        
        # 5. Delete session
        success = db.delete_session(session_id, db_path=self.db_path)
        self.assertTrue(success)
        retrieved = db.get_session(session_id, db_path=self.db_path)
        self.assertIsNone(retrieved)

    def test_chat_history(self):
        session_id = "test-session-history"
        db.create_session(session_id, title="History Test", mode="work", db_path=self.db_path)
        
        # Save messages
        msg1_id = db.save_message(session_id, role="user", content="Hello, Firefly!", mode="work", db_path=self.db_path)
        msg2_id = db.save_message(session_id, role="assistant", content="Hello, custom assistant!", mode="work", emotion="happy", db_path=self.db_path)
        
        self.assertGreater(msg1_id, 0)
        self.assertGreater(msg2_id, 0)
        
        # Load history
        history = db.load_history(session_id, limit=10, db_path=self.db_path)
        self.assertEqual(len(history), 2)
        
        # Verify order (should be positive chronological order: user first, then assistant)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[0]["content"], "Hello, Firefly!")
        self.assertEqual(history[1]["role"], "assistant")
        self.assertEqual(history[1]["content"], "Hello, custom assistant!")
        self.assertEqual(history[1]["emotion"], "happy")
        
        # Clear history
        db.clear_history(session_id, db_path=self.db_path)
        history_after = db.load_history(session_id, limit=10, db_path=self.db_path)
        self.assertEqual(len(history_after), 0)

    def test_memories(self):
        memory_id = "mem-12345"
        
        # Save memory
        memory = db.save_memory(
            memory_id=memory_id,
            mem_type="preference",
            content="Likes spicy food",
            namespace="daily_life",
            confidence=0.95,
            db_path=self.db_path
        )
        self.assertEqual(memory["id"], memory_id)
        self.assertEqual(memory["content"], "Likes spicy food")
        self.assertEqual(memory["confidence"], 0.95)
        
        # Query memories with confidence filter
        results = db.query_memories("daily_life", min_confidence=0.90, db_path=self.db_path)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], memory_id)
        
        # Query memories with strict confidence filter
        results_strict = db.query_memories("daily_life", min_confidence=0.98, db_path=self.db_path)
        self.assertEqual(len(results_strict), 0)
        
        # Update memory
        success = db.update_memory(memory_id, content="Likes sweet food", confidence=0.88, db_path=self.db_path)
        self.assertTrue(success)
        
        results_updated = db.query_memories("daily_life", min_confidence=0.85, db_path=self.db_path)
        self.assertEqual(len(results_updated), 1)
        self.assertEqual(results_updated[0]["content"], "Likes sweet food")
        self.assertEqual(results_updated[0]["confidence"], 0.88)
        
        # Delete memory
        success = db.delete_memory(memory_id, db_path=self.db_path)
        self.assertTrue(success)
        results_deleted = db.query_memories("daily_life", min_confidence=0.0, db_path=self.db_path)
        self.assertEqual(len(results_deleted), 0)

    def test_active_concern(self):
        trigger = "first_chat"
        content = "Good morning!"
        mode = "daily"
        
        # Add concern
        concern_id = db.add_concern(trigger, content, mode, db_path=self.db_path)
        self.assertGreater(concern_id, 0)
        
        # Get recent concern
        recent = db.get_recent_concern(trigger, mode, db_path=self.db_path)
        self.assertIsNotNone(recent)
        self.assertEqual(recent["content"], content)
        self.assertEqual(recent["trigger"], trigger)
        
        # Test since_ms filter
        now_ms = int(time.time() * 1000)
        recent_filtered = db.get_recent_concern(trigger, mode, since_ms=now_ms + 10000, db_path=self.db_path)
        self.assertIsNone(recent_filtered)

if __name__ == "__main__":
    unittest.main()
