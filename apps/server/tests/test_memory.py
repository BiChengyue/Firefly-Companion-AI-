import os
import unittest
import tempfile
import asyncio
from pathlib import Path
import app.core.db as db
from app.core.memory.manager import MemoryManager, ActiveConcern
from app.core.llm.base import LLMMessage

class MockLLMResponse:
    def __init__(self, content: str):
        self.content = content

class MockLLMProvider:
    def __init__(self, return_content: str):
        self.return_content = return_content
        self.chat_calls = []

    async def chat(self, messages, temperature=0.3, max_tokens=1024):
        self.chat_calls.append({
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        })
        return MockLLMResponse(self.return_content)

class TestMemoryAndConcern(unittest.TestCase):
    def setUp(self):
        # Create a temporary database file and patch the default DB path
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.db_fd)
        
        self.old_db_path = db._DEFAULT_DB_PATH
        db._DEFAULT_DB_PATH = self.db_path
        if hasattr(db._local, "__dict__"):
            db._local.__dict__.clear()
        
        self.manager = MemoryManager()
        self.concern = ActiveConcern()

    def tearDown(self):
        # Restore old default DB path
        db._DEFAULT_DB_PATH = self.old_db_path
        if hasattr(db._local, "__dict__"):
            for k, conn in list(db._local.__dict__.items()):
                try:
                    if hasattr(conn, "close"):
                        conn.close()
                except Exception:
                    pass
            db._local.__dict__.clear()
        try:
            if os.path.exists(self.db_path):
                os.remove(self.db_path)
        except OSError:
            pass

    def test_short_term_memory(self):
        # Test sliding window size
        self.manager.settings.memory.short_term_window = 3
        
        self.manager.add_message("user", "Hello")
        self.manager.add_message("assistant", "Hi there")
        self.manager.add_message("user", "How are you?")
        
        short_term = self.manager.get_short_term()
        self.assertEqual(len(short_term), 3)
        self.assertEqual(short_term[0]["content"], "Hello")
        
        # Add a 4th message - should push out "Hello"
        self.manager.add_message("assistant", "I am good")
        short_term_after = self.manager.get_short_term()
        self.assertEqual(len(short_term_after), 3)
        self.assertEqual(short_term_after[0]["content"], "Hi there")
        self.assertEqual(short_term_after[2]["content"], "I am good")
        
        # Test clear
        self.manager.clear_short_term()
        self.assertEqual(len(self.manager.get_short_term()), 0)

    def test_namespaces(self):
        # Daily mode namespace config
        daily_ns = self.manager.get_namespaces("daily")
        self.assertIn("shared_profile", daily_ns)
        self.assertIn("daily_life", daily_ns)
        self.assertNotIn("work_tasks", daily_ns)
        
        # Work mode namespace config
        work_ns = self.manager.get_namespaces("work")
        self.assertIn("shared_profile", work_ns)
        self.assertIn("work_tasks", work_ns)
        self.assertNotIn("daily_life", work_ns)

    def test_long_term_recall(self):
        # Pre-seed some memories in SQLite
        db.save_memory("m1", "preference", "User likes green tea", "daily_life", 0.95, db_path=self.db_path)
        db.save_memory("m2", "preference", "User dislikes coffee", "daily_life", 0.70, db_path=self.db_path)
        db.save_memory("m3", "work_tasks", "Fix memory bugs", "work_tasks", 0.90, db_path=self.db_path)
        
        # Test recall in daily mode with min_confidence threshold of 0.80 (default in settings usually)
        self.manager.settings.memory.confidence_threshold = 0.80
        
        # Query with keyword matching (e.g. "tea")
        recalled = asyncio.run(self.manager.recall("tea", mode="daily", top_k=1))
        self.assertEqual(len(recalled), 1)
        self.assertEqual(recalled[0]["id"], "m1")
        
        # Query with keyword matching (e.g. "coffee") - should not return m2 due to confidence threshold 0.80 (m2 is 0.70)
        recalled_low = asyncio.run(self.manager.recall("coffee", mode="daily"))
        self.assertNotIn("m2", [m["id"] for m in recalled_low])
        
        # Query in daily mode for a work task memory - should not return m3 due to namespace isolation
        recalled_work = asyncio.run(self.manager.recall("bugs", mode="daily"))
        self.assertNotIn("m3", [m["id"] for m in recalled_work])
        
        # Query in work mode for the work task memory
        recalled_work_ok = asyncio.run(self.manager.recall("bugs", mode="work"))
        self.assertEqual(len(recalled_work_ok), 1)
        self.assertEqual(recalled_work_ok[0]["id"], "m3")

    def test_write_long_term_threshold(self):
        self.manager.settings.memory.confidence_threshold = 0.85
        
        # Should write because confidence 0.90 >= 0.85
        ok = asyncio.run(self.manager.write_long_term("High confidence info", {"type": "user_profile"}, 0.90, "shared_profile"))
        self.assertTrue(ok)
        
        # Should NOT write because confidence 0.75 < 0.85
        not_ok = asyncio.run(self.manager.write_long_term("Low confidence info", {"type": "user_profile"}, 0.75, "shared_profile"))
        self.assertFalse(not_ok)
        
        # Verify db contents
        mems = db.query_memories("shared_profile", min_confidence=0.0, db_path=self.db_path)
        self.assertEqual(len(mems), 1)
        self.assertEqual(mems[0]["content"], "High confidence info")

    def test_extract_memories(self):
        self.manager.settings.memory.long_term_enabled = True
        self.manager.settings.memory.confidence_threshold = 0.85
        
        # Mock LLM response containing memory list
        mock_json = '{"memories": [{"type": "preference", "content": "Likes reading sci-fi books", "confidence": 0.92}, {"type": "event", "content": "Had coffee at 9am", "confidence": 0.60}]}'
        mock_provider = MockLLMProvider(return_content=mock_json)
        
        recent_messages = [
            LLMMessage(role="user", content="I love reading sci-fi books."),
            LLMMessage(role="assistant", content="That's awesome!")
        ]
        
        saved_count = asyncio.run(self.manager.extract_memories(mock_provider, recent_messages, mode="daily"))
        
        # Only the sci-fi books memory should be saved because its confidence (0.92) is >= threshold (0.85)
        # The coffee memory has confidence 0.60, which is below the threshold.
        self.assertEqual(saved_count, 1)
        
        # Verify it was saved to the SQLite db in the 'daily_life' namespace
        mems = db.query_memories("daily_life", min_confidence=0.80, db_path=self.db_path)
        self.assertEqual(len(mems), 1)
        self.assertEqual(mems[0]["content"], "Likes reading sci-fi books")
        self.assertEqual(mems[0]["confidence"], 0.92)

    def test_active_concern_flow(self):
        # Initially, should fire
        self.assertTrue(self.concern.should_fire("first_chat", "daily"))
        
        # Record the fire
        self.concern.record("first_chat", "Hope you have a nice day!", "daily")
        
        # Now, should NOT fire (daily rate limit/deduplication)
        self.assertFalse(self.concern.should_fire("first_chat", "daily"))

if __name__ == "__main__":
    unittest.main()
