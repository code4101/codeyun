
import unittest
import os
import shutil
import tempfile
import sys
import json
import time

# Add backend directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.task_manager import TaskManager, TaskStore, TaskConfig

class TestTaskManager(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for test data
        self.test_dir = tempfile.mkdtemp()
        self.test_file = os.path.join(self.test_dir, "test_tasks.json")
        
        # Initialize TaskStore with the temporary file
        self.store = TaskStore(file_path=self.test_file)
        self.manager = TaskManager(store=self.store)

    def tearDown(self):
        # Clean up temporary directory
        shutil.rmtree(self.test_dir)

    def test_create_and_load_task(self):
        # Test creating a task
        task = TaskConfig(
            id="test-task-1",
            name="Test Task",
            command="echo hello",
            created_at=time.time(),
            device_id="local"
        )
        self.store.add_task(task)
        
        # Verify it's in memory
        self.assertEqual(len(self.store.get_all_tasks()), 1)
        self.assertEqual(self.store.get_task("test-task-1").name, "Test Task")
        
        # Verify persistence
        new_store = TaskStore(file_path=self.test_file)
        self.assertEqual(len(new_store.get_all_tasks()), 1)
        self.assertEqual(new_store.get_task("test-task-1").command, "echo hello")

    def test_update_task(self):
        # Create initial task
        task = TaskConfig(
            id="test-task-2",
            name="Original Name",
            command="echo original",
            created_at=time.time()
        )
        self.store.add_task(task)
        
        # Update task
        task.name = "Updated Name"
        self.store.save()
        
        # Verify persistence
        new_store = TaskStore(file_path=self.test_file)
        loaded_task = new_store.get_task("test-task-2")
        self.assertEqual(loaded_task.name, "Updated Name")

    def test_delete_task(self):
        task = TaskConfig(
            id="test-task-3",
            name="To Delete",
            command="echo delete",
            created_at=time.time()
        )
        self.store.add_task(task)
        
        self.store.remove_task("test-task-3")
        self.assertIsNone(self.store.get_task("test-task-3"))
        
        # Verify persistence
        new_store = TaskStore(file_path=self.test_file)
        self.assertEqual(len(new_store.get_all_tasks()), 0)

    def test_corrupted_file_recovery(self):
        # Create a corrupted file
        with open(self.test_file, 'w') as f:
            f.write("{invalid json")
            
        # Should not crash and should backup
        store = TaskStore(file_path=self.test_file)
        self.assertEqual(len(store.get_all_tasks()), 0)
        
        # Check if backup was created (implementation dependent, may need adjustment based on TaskStore code)
        # Note: The current implementation only backups on save, not on load failure. 
        # But load failure should not overwrite the file.
        
        # Try to save a new task
        task = TaskConfig(
            id="new-task",
            name="New Task",
            command="echo new",
            created_at=time.time()
        )
        store.add_task(task)
        
        # Check if backup exists (since we saved)
        self.assertTrue(os.path.exists(self.test_file + ".bak"))

if __name__ == '__main__':
    unittest.main()
