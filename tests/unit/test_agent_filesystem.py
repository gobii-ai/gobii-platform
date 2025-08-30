from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.utils import timezone
from unittest.mock import patch, MagicMock
import uuid
import tempfile
import os

from api.models import (
    PersistentAgent, 
    BrowserUseAgent, 
    UserQuota,
    AgentFileSpace,
    AgentFileSpaceAccess,
    AgentFsNode
)


class AgentFileSpaceModelTests(TestCase):
    """Test suite for the AgentFileSpace model."""

    @classmethod
    def setUpTestData(cls):
        """Set up non-modified objects used by all test methods."""
        User = get_user_model()
        cls.user = User.objects.create_user(
            username='testuser@example.com', 
            email='testuser@example.com', 
            password='password'
        )
        # UserQuota is created by a signal, but we can get it and increase the limit for tests.
        quota, _ = UserQuota.objects.get_or_create(user=cls.user)
        quota.agent_limit = 100  # Set a high limit for testing purposes
        quota.save()

    def test_filespace_creation(self):
        """Test that an AgentFileSpace can be created successfully."""
        filespace = AgentFileSpace.objects.create(
            name="Test Workspace",
            owner_user=self.user,
            description="A test workspace for unit tests"
        )
        
        self.assertEqual(AgentFileSpace.objects.count(), 1)
        self.assertEqual(filespace.name, "Test Workspace")
        self.assertEqual(filespace.owner_user, self.user)
        self.assertEqual(filespace.description, "A test workspace for unit tests")
        self.assertIsNotNone(filespace.id)
        self.assertIsNotNone(filespace.created_at)
        self.assertIsNotNone(filespace.updated_at)

    def test_filespace_unique_name_per_user(self):
        """Test that filespace names must be unique per user."""
        # Create first filespace
        AgentFileSpace.objects.create(
            name="Workspace",
            owner_user=self.user
        )
        
        # Try to create another filespace with the same name for the same user
        with self.assertRaises(Exception):  # Should raise an IntegrityError
            AgentFileSpace.objects.create(
                name="Workspace",
                owner_user=self.user
            )

    def test_filespace_same_name_different_users(self):
        """Test that different users can have filespaces with the same name."""
        User = get_user_model()
        user2 = User.objects.create_user(
            username='testuser2@example.com',
            email='testuser2@example.com',
            password='password'
        )
        
        # Create filespace for first user
        fs1 = AgentFileSpace.objects.create(
            name="Workspace",
            owner_user=self.user
        )
        
        # Create filespace with same name for second user - should work
        fs2 = AgentFileSpace.objects.create(
            name="Workspace",
            owner_user=user2
        )
        
        self.assertEqual(fs1.name, fs2.name)
        self.assertNotEqual(fs1.owner_user, fs2.owner_user)

    def test_filespace_str_representation(self):
        """Test the string representation of AgentFileSpace."""
        filespace = AgentFileSpace.objects.create(
            name="My Workspace",
            owner_user=self.user
        )
        
        expected = f"FileSpace<My Workspace> ({filespace.id})"
        self.assertEqual(str(filespace), expected)


class AgentFileSpaceAccessModelTests(TestCase):
    """Test suite for the AgentFileSpaceAccess model."""

    @classmethod
    def setUpTestData(cls):
        """Set up non-modified objects used by all test methods."""
        User = get_user_model()
        cls.user = User.objects.create_user(
            username='testuser@example.com',
            email='testuser@example.com',
            password='password'
        )
        # UserQuota is created by a signal, but we can get it and increase the limit for tests.
        quota, _ = UserQuota.objects.get_or_create(user=cls.user)
        quota.agent_limit = 100
        quota.save()

    def setUp(self):
        """Set up objects for each test method."""
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user, 
            name="test-browser-agent"
        )
        self.persistent_agent = PersistentAgent.objects.create(
            user=self.user,
            name="test-agent",
            charter="Test charter",
            browser_use_agent=self.browser_agent
        )
        self.filespace = AgentFileSpace.objects.create(
            name="Test Workspace",
            owner_user=self.user
        )

    def test_access_creation(self):
        """Test that AgentFileSpaceAccess can be created successfully."""
        access = AgentFileSpaceAccess.objects.create(
            filespace=self.filespace,
            agent=self.persistent_agent,
            role=AgentFileSpaceAccess.Role.WRITER,
            is_default=False  # Can't be default since agent already has one from signal
        )
        
        self.assertEqual(access.filespace, self.filespace)
        self.assertEqual(access.agent, self.persistent_agent)
        self.assertEqual(access.role, AgentFileSpaceAccess.Role.WRITER)
        self.assertFalse(access.is_default)
        self.assertIsNotNone(access.granted_at)

    def test_access_roles(self):
        """Test the different access roles."""
        # Test all roles
        roles = [
            AgentFileSpaceAccess.Role.OWNER,
            AgentFileSpaceAccess.Role.WRITER,
            AgentFileSpaceAccess.Role.READER
        ]
        
        for i, role in enumerate(roles):
            with self.subTest(role=role):
                # Create a unique filespace for each test to avoid constraint violations
                fs = AgentFileSpace.objects.create(
                    name=f"Test Workspace {i}",
                    owner_user=self.user
                )
                access = AgentFileSpaceAccess.objects.create(
                    filespace=fs,
                    agent=self.persistent_agent,
                    role=role
                )
                self.assertEqual(access.role, role)

    def test_access_unique_per_filespace_agent(self):
        """Test that an agent can only have one access record per filespace."""
        # Create first access
        AgentFileSpaceAccess.objects.create(
            filespace=self.filespace,
            agent=self.persistent_agent,
            role=AgentFileSpaceAccess.Role.READER
        )
        
        # Try to create another access for the same agent and filespace
        with self.assertRaises(Exception):  # Should raise an IntegrityError
            AgentFileSpaceAccess.objects.create(
                filespace=self.filespace,
                agent=self.persistent_agent,
                role=AgentFileSpaceAccess.Role.WRITER
            )

    def test_access_str_representation(self):
        """Test the string representation of AgentFileSpaceAccess."""
        access = AgentFileSpaceAccess.objects.create(
            filespace=self.filespace,
            agent=self.persistent_agent,
            role=AgentFileSpaceAccess.Role.OWNER
        )
        
        expected = f"Access<{self.persistent_agent.name}→{self.filespace.name}:OWNER>"
        self.assertEqual(str(access), expected)

    def test_unique_default_filespace_per_agent(self):
        """Test that only one default filespace is allowed per agent."""
        # The persistent agent already has a default filespace created by the signal
        # Verify that a default access exists
        default_access = AgentFileSpaceAccess.objects.filter(
            agent=self.persistent_agent,
            is_default=True
        ).first()
        self.assertIsNotNone(default_access, "Agent should have a default filespace from signal")
        
        # Create another filespace for the same agent
        second_filespace = AgentFileSpace.objects.create(
            name="Second Workspace",
            owner_user=self.user
        )
        
        # Try to create another default access for the same agent
        # This should fail due to the unique constraint
        from django.db import IntegrityError, transaction
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                AgentFileSpaceAccess.objects.create(
                    filespace=second_filespace,
                    agent=self.persistent_agent,
                    role=AgentFileSpaceAccess.Role.WRITER,
                    is_default=True
                )
        
        # Verify that creating a non-default access still works (in a new transaction)
        AgentFileSpaceAccess.objects.create(
            filespace=second_filespace,
            agent=self.persistent_agent,
            role=AgentFileSpaceAccess.Role.WRITER,
            is_default=False
        )


class AgentFsNodeModelTests(TestCase):
    """Test suite for the AgentFsNode model."""

    @classmethod
    def setUpTestData(cls):
        """Set up non-modified objects used by all test methods."""
        User = get_user_model()
        cls.user = User.objects.create_user(
            username='testuser@example.com',
            email='testuser@example.com',
            password='password'
        )
        # UserQuota is created by a signal, but we can get it and increase the limit for tests.
        quota, _ = UserQuota.objects.get_or_create(user=cls.user)
        quota.agent_limit = 100
        quota.save()

    def setUp(self):
        """Set up objects for each test method."""
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="test-browser-agent"
        )
        self.persistent_agent = PersistentAgent.objects.create(
            user=self.user,
            name="test-agent",
            charter="Test charter",
            browser_use_agent=self.browser_agent
        )
        self.filespace = AgentFileSpace.objects.create(
            name="Test Workspace",
            owner_user=self.user
        )

    def test_directory_creation(self):
        """Test that a directory node can be created successfully."""
        directory = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="documents",
            created_by_agent=self.persistent_agent
        )
        directory.save()
        
        self.assertEqual(directory.filespace, self.filespace)
        self.assertEqual(directory.node_type, AgentFsNode.NodeType.DIR)
        self.assertEqual(directory.name, "documents")
        self.assertEqual(directory.path, "/documents")
        self.assertEqual(directory.created_by_agent, self.persistent_agent)
        self.assertIsNone(directory.parent)
        self.assertFalse(directory.content)  # FileField evaluates to False when empty
        self.assertTrue(directory.is_dir)
        self.assertFalse(directory.is_file)

    def test_file_creation(self):
        """Test that a file node can be created successfully."""
        # Create a temporary file for testing
        content = ContentFile(b"Hello, world!", name="hello.txt")
        
        file_node = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.FILE,
            name="hello.txt",
            content=content,
            mime_type="text/plain",
            created_by_agent=self.persistent_agent
        )
        
        self.assertEqual(file_node.filespace, self.filespace)
        self.assertEqual(file_node.node_type, AgentFsNode.NodeType.FILE)
        self.assertEqual(file_node.name, "hello.txt")
        self.assertEqual(file_node.path, "/hello.txt")
        self.assertEqual(file_node.mime_type, "text/plain")
        self.assertIsNotNone(file_node.content)
        self.assertFalse(file_node.is_dir)
        self.assertTrue(file_node.is_file)

    def test_nested_directory_structure(self):
        """Test creating nested directories and files."""
        # Create root directory
        root_dir = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="projects"
        )
        root_dir.save()
        
        # Create subdirectory
        sub_dir = AgentFsNode(
            filespace=self.filespace,
            parent=root_dir,
            node_type=AgentFsNode.NodeType.DIR,
            name="webapp"
        )
        sub_dir.save()
        
        # Create file in subdirectory
        content = ContentFile(b"console.log('Hello');", name="app.js")
        file_node = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=sub_dir,
            node_type=AgentFsNode.NodeType.FILE,
            name="app.js",
            content=content
        )
        
        # Check paths are computed correctly
        self.assertEqual(root_dir.path, "/projects")
        self.assertEqual(sub_dir.path, "/projects/webapp")
        self.assertEqual(file_node.path, "/projects/webapp/app.js")

    def test_path_computation(self):
        """Test that paths are computed correctly for deeply nested structures."""
        # Create a deep directory structure: /a/b/c/d/e
        current_parent = None
        nodes = []
        
        for name in ['a', 'b', 'c', 'd', 'e']:
            node = AgentFsNode(
                filespace=self.filespace,
                parent=current_parent,
                node_type=AgentFsNode.NodeType.DIR,
                name=name
            )
            node.save()
            nodes.append(node)
            current_parent = node
        
        # Check that paths are computed correctly
        expected_paths = ["/a", "/a/b", "/a/b/c", "/a/b/c/d", "/a/b/c/d/e"]
        for node, expected_path in zip(nodes, expected_paths):
            self.assertEqual(node.path, expected_path)

    def test_path_update_on_rename(self):
        """Test that paths are updated correctly when directories are renamed."""
        # Create directory structure: /docs/projects/webapp/
        docs = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="docs"
        )
        docs.save()
        
        projects = AgentFsNode(
            filespace=self.filespace,
            parent=docs,
            node_type=AgentFsNode.NodeType.DIR,
            name="projects"
        )
        projects.save()
        
        webapp = AgentFsNode(
            filespace=self.filespace,
            parent=projects,
            node_type=AgentFsNode.NodeType.DIR,
            name="webapp"
        )
        webapp.save()
        
        # Rename the middle directory
        projects.name = "applications"
        projects.save()
        
        # Refresh from database and check paths updated
        webapp.refresh_from_db()
        projects.refresh_from_db()
        
        self.assertEqual(projects.path, "/docs/applications")
        self.assertEqual(webapp.path, "/docs/applications/webapp")

    def test_unique_name_per_directory(self):
        """Test that names must be unique within a directory."""
        # Create a directory
        parent_dir = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="parent"
        )
        parent_dir.save()
        
        # Create first child
        child1 = AgentFsNode(
            filespace=self.filespace,
            parent=parent_dir,
            node_type=AgentFsNode.NodeType.DIR,
            name="child"
        )
        child1.save()
        
        # Try to create another child with the same name in the same directory
        with self.assertRaises(Exception):  # Should raise an IntegrityError
            child2 = AgentFsNode(
                filespace=self.filespace,
                parent=parent_dir,
                node_type=AgentFsNode.NodeType.DIR,
                name="child"
            )
            child2.save()

    def test_same_name_different_directories(self):
        """Test that the same name can exist in different directories."""
        # Create two directories
        dir1 = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="dir1"
        )
        dir1.save()
        
        dir2 = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="dir2"
        )
        dir2.save()
        
        # Create children with the same name in different directories - should work
        child1 = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=dir1,
            node_type=AgentFsNode.NodeType.FILE,
            name="readme.txt",
            content=ContentFile(b"Content 1", name="readme.txt")
        )
        
        child2 = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=dir2,
            node_type=AgentFsNode.NodeType.FILE,
            name="readme.txt",
            content=ContentFile(b"Content 2", name="readme.txt")
        )
        
        self.assertEqual(child1.name, child2.name)
        self.assertEqual(child1.path, "/dir1/readme.txt")
        self.assertEqual(child2.path, "/dir2/readme.txt")

    def test_name_validation(self):
        """Test validation of node names."""
        # Test invalid names
        invalid_names = [
            "",           # Empty name
            "file/name",  # Contains path separator
            "file\x00",   # Contains null byte
        ]
        
        for invalid_name in invalid_names:
            with self.subTest(name=invalid_name):
                node = AgentFsNode(
                    filespace=self.filespace,
                    node_type=AgentFsNode.NodeType.DIR,
                    name=invalid_name
                )
                with self.assertRaises(ValidationError):
                    node.full_clean()

    def test_parent_validation(self):
        """Test validation of parent relationships."""
        # Create a file node
        file_node = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.FILE,
            name="file.txt",
            content=ContentFile(b"content", name="file.txt")
        )
        
        # Try to create a directory with a file as parent - should fail
        child = AgentFsNode(
            filespace=self.filespace,
            parent=file_node,
            node_type=AgentFsNode.NodeType.DIR,
            name="child"
        )
        with self.assertRaises(ValidationError):
            child.full_clean()

    def test_prevent_cycles(self):
        """Test that cycles in the directory structure are prevented."""
        # Create a directory structure: A -> B
        dir_a = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="a"
        )
        dir_a.save()
        
        dir_b = AgentFsNode(
            filespace=self.filespace,
            parent=dir_a,
            node_type=AgentFsNode.NodeType.DIR,
            name="b"
        )
        dir_b.save()
        
        # Try to create a cycle by making A a child of B
        dir_a.parent = dir_b
        with self.assertRaises(ValidationError):
            dir_a.full_clean()

    def test_directory_content_constraints(self):
        """Test that directories cannot have content."""
        # First test: directory clean method should clear content
        directory = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="dir"
        )
        # Manually set content to test the constraint
        directory.content = ContentFile(b"content", name="content.txt")
        
        # The clean method should clear the content for directories
        directory.clean()
        self.assertFalse(directory.content)  # FileField evaluates to False when empty
        
        # Test that we can save a directory without content
        directory.save()
        self.assertEqual(directory.node_type, AgentFsNode.NodeType.DIR)

    def test_file_size_computation(self):
        """Test that file sizes are computed correctly."""
        content = ContentFile(b"Hello, world! This is a test file.", name="test.txt")
        
        file_node = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.FILE,
            name="test.txt",
            content=content
        )
        
        self.assertEqual(file_node.size_bytes, len(b"Hello, world! This is a test file."))

    def test_soft_delete(self):
        """Test the soft delete functionality."""
        node = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="to_delete"
        )
        node.save()
        
        # Initially not deleted
        self.assertFalse(node.is_deleted)
        self.assertIsNone(node.deleted_at)
        
        # Mark as deleted
        node.is_deleted = True
        node.save()
        
        # Should have deleted timestamp
        self.assertTrue(node.is_deleted)
        self.assertIsNotNone(node.deleted_at)

    def test_queryset_methods(self):
        """Test the custom queryset methods."""
        # Create some nodes
        dir1 = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="dir1"
        )
        dir1.save()
        
        file1 = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.FILE,
            name="file1.txt",
            content=ContentFile(b"content", name="file1.txt")
        )
        
        deleted_node = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="deleted",
            is_deleted=True,
            deleted_at=timezone.now()
        )
        deleted_node.save()
        
        # Test alive() method
        alive_nodes = AgentFsNode.objects.alive()
        self.assertIn(dir1, alive_nodes)
        self.assertIn(file1, alive_nodes)
        self.assertNotIn(deleted_node, alive_nodes)
        
        # Test directories() method
        directories = AgentFsNode.objects.directories()
        self.assertIn(dir1, directories)
        self.assertNotIn(file1, directories)
        
        # Test files() method
        files = AgentFsNode.objects.files()
        self.assertIn(file1, files)
        self.assertNotIn(dir1, files)
        
        # Test in_dir() method
        child = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=dir1,
            node_type=AgentFsNode.NodeType.FILE,
            name="child.txt",
            content=ContentFile(b"content", name="child.txt")
        )
        
        children = AgentFsNode.objects.in_dir(dir1)
        self.assertIn(child, children)
        self.assertNotIn(file1, children)

    def test_str_representation(self):
        """Test the string representation of nodes."""
        directory = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="documents"
        )
        directory.save()
        
        file_node = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.FILE,
            name="readme.txt",
            content=ContentFile(b"content", name="readme.txt")
        )
        
        self.assertEqual(str(directory), "DIR /documents")
        self.assertEqual(str(file_node), "FILE /readme.txt")

    def test_object_key_for_method(self):
        """Test the object_key_for method for generating object store keys."""
        # Create a node (doesn't need to be saved for this test)
        node = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.FILE,
            name="test_file.txt"
        )
        
        # Test with default filename (uses node name)
        key = node.object_key_for()
        expected = f"agent_fs/{self.filespace.id}/{node.id}/test_file.txt"
        self.assertEqual(key, expected)
        
        # Test with explicit filename
        key = node.object_key_for("custom_name.txt")
        expected = f"agent_fs/{self.filespace.id}/{node.id}/custom_name.txt"
        self.assertEqual(key, expected)
        
        # Test with filename that has path separators (should use basename)
        key = node.object_key_for("/path/to/deep_file.txt")
        expected = f"agent_fs/{self.filespace.id}/{node.id}/deep_file.txt"
        self.assertEqual(key, expected)
        
        # Test with filename that needs sanitization
        key = node.object_key_for("file with spaces & special chars!.txt")
        expected = f"agent_fs/{self.filespace.id}/{node.id}/file_with_spaces__special_chars.txt"
        self.assertEqual(key, expected)
        
        # Test with None filename and no node name (should fallback to "file")
        node.name = None
        key = node.object_key_for(None)
        expected = f"agent_fs/{self.filespace.id}/{node.id}/file"
        self.assertEqual(key, expected)
        
        # Test with empty string filename (should fallback to "file")
        key = node.object_key_for("")
        expected = f"agent_fs/{self.filespace.id}/{node.id}/file"
        self.assertEqual(key, expected)

    def test_object_key_property_with_content(self):
        """Test the object_key property when node has content."""
        # Create and save a file node with content
        content = ContentFile(b"test content", name="test_file.txt")
        node = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.FILE,
            name="original_name.txt",
            content=content
        )
        
        # The object_key should return the actual content name
        self.assertIsNotNone(node.object_key)
        # The content file uses the upload_to path, and the filename comes from the ContentFile
        self.assertTrue(node.object_key.endswith("test_file.txt"))
        self.assertTrue(node.object_key.startswith(f"agent_fs/{self.filespace.id}/{node.id}/"))

    def test_object_key_property_without_content(self):
        """Test the object_key property when node has no content."""
        # Create a file node without content
        node = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.FILE,
            name="no_content.txt"
        )
        node.save()
        
        # The object_key should fallback to object_key_for()
        expected = f"agent_fs/{self.filespace.id}/{node.id}/no_content.txt"
        self.assertEqual(node.object_key, expected)

    def test_object_key_property_directory(self):
        """Test the object_key property for directories."""
        # Create a directory
        directory = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="test_dir"
        )
        directory.save()
        
        # Directories don't have content, so should use object_key_for()
        expected = f"agent_fs/{self.filespace.id}/{directory.id}/test_dir"
        self.assertEqual(directory.object_key, expected)

    def test_object_key_edge_cases(self):
        """Test object key generation with various edge cases."""
        # Test with Unicode filename
        node = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.FILE,
            name="тест.txt"  # Cyrillic characters
        )
        node.save()
        
        key = node.object_key_for()
        expected = f"agent_fs/{self.filespace.id}/{node.id}/тест.txt"
        self.assertEqual(key, expected)
        
        # Test with very long filename
        long_name = "a" * 200 + ".txt"
        key = node.object_key_for(long_name)
        # get_valid_filename should handle long names appropriately
        self.assertTrue(key.startswith(f"agent_fs/{self.filespace.id}/{node.id}/"))
        
        # Test with filename containing only invalid characters (should fallback)
        key = node.object_key_for("///")
        # Should use the node's name as fallback
        expected = f"agent_fs/{self.filespace.id}/{node.id}/тест.txt"
        self.assertEqual(key, expected)

    def test_object_key_consistency(self):
        """Test that object keys are consistent across multiple calls."""
        node = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.FILE,
            name="consistency_test.txt"
        )
        node.save()
        
        # Multiple calls should return the same key
        key1 = node.object_key_for("test.txt")
        key2 = node.object_key_for("test.txt")
        key3 = node.object_key
        
        self.assertEqual(key1, key2)
        
        # object_key property should be consistent too
        prop_key1 = node.object_key
        prop_key2 = node.object_key
        self.assertEqual(prop_key1, prop_key2)

    def test_root_level_name_collision_prevention(self):
        """Test that root-level nodes cannot have duplicate names in the same filespace."""
        # Create first root-level directory
        root_dir1 = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="foo"
        )
        root_dir1.save()
        
        # Try to create another root-level directory with the same name
        root_dir2 = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="foo"
        )
        
        # This should fail due to the root-level unique constraint
        with self.assertRaises(Exception):  # Should raise an IntegrityError
            root_dir2.save()

    def test_root_level_file_name_collision_prevention(self):
        """Test that root-level files cannot have duplicate names in the same filespace."""
        # Create first root-level file
        root_file1 = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.FILE,
            name="config.txt",
            content=ContentFile(b"content1", name="config.txt")
        )
        
        # Try to create another root-level file with the same name
        with self.assertRaises(Exception):  # Should raise an IntegrityError
            AgentFsNode.objects.create(
                filespace=self.filespace,
                node_type=AgentFsNode.NodeType.FILE,
                name="config.txt",
                content=ContentFile(b"content2", name="config.txt")
            )

    def test_root_level_mixed_types_name_collision_prevention(self):
        """Test that root-level directories and files cannot have the same name."""
        # Create root-level directory
        root_dir = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="shared_name"
        )
        root_dir.save()
        
        # Try to create root-level file with the same name
        with self.assertRaises(Exception):  # Should raise an IntegrityError
            AgentFsNode.objects.create(
                filespace=self.filespace,
                node_type=AgentFsNode.NodeType.FILE,
                name="shared_name",
                content=ContentFile(b"content", name="shared_name")
            )

    def test_root_level_same_name_different_filespaces_allowed(self):
        """Test that root-level nodes can have the same name in different filespaces."""
        # Create second filespace
        second_filespace = AgentFileSpace.objects.create(
            name="Second Workspace",
            owner_user=self.user
        )
        
        # Create root-level directory in first filespace
        root_dir1 = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="shared"
        )
        root_dir1.save()
        
        # Create root-level directory with same name in second filespace - should work
        root_dir2 = AgentFsNode(
            filespace=second_filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="shared"
        )
        root_dir2.save()
        
        self.assertEqual(root_dir1.name, root_dir2.name)
        self.assertNotEqual(root_dir1.filespace, root_dir2.filespace)
        self.assertEqual(root_dir1.path, "/shared")
        self.assertEqual(root_dir2.path, "/shared")

    def test_root_level_constraint_edge_cases(self):
        """Test edge cases for the root-level unique constraint."""
        # Case 1: Case sensitivity - different cases should be allowed at root level  
        root_dir1 = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="Test"
        )
        root_dir1.save()
        
        root_dir2 = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="test"  # different case
        )
        root_dir2.save()  # Should work since constraint is case-sensitive
        
        self.assertEqual(root_dir1.path, "/Test")
        self.assertEqual(root_dir2.path, "/test")
        
        # Case 2: Moving from subdirectory to root should still respect root constraint
        subdir = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="parent"
        )
        subdir.save()
        
        child = AgentFsNode(
            filespace=self.filespace,
            parent=subdir,
            node_type=AgentFsNode.NodeType.DIR,
            name="Test"  # Same name as root_dir1
        )
        child.save()
        
        # Moving child to root should fail due to name collision
        child.parent = None
        with self.assertRaises(Exception):  # Should raise IntegrityError
            child.save()

    def test_root_level_constraint_after_deletion(self):
        """Test that root-level constraint works properly after deletions."""
        # Create root-level node
        root_node = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="temporary"
        )
        root_node.save()
        
        # Soft delete it
        root_node.is_deleted = True
        root_node.save()
        
        # Should be able to create another root node with the same name 
        # (since deleted nodes shouldn't block new ones)
        new_root_node = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="temporary"
        )
        new_root_node.save()  # Should work
        
        self.assertEqual(new_root_node.path, "/temporary")

    def test_existing_non_root_nodes_unaffected(self):
        """Test that existing non-root unique constraints still work."""
        # Create parent directory
        parent = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="parent"
        )
        parent.save()
        
        # Create first child
        child1 = AgentFsNode(
            filespace=self.filespace,
            parent=parent,
            node_type=AgentFsNode.NodeType.FILE,
            name="document.txt",
            content=ContentFile(b"content1", name="document.txt")
        )
        child1.save()
        
        # Try to create duplicate child - should still fail
        with self.assertRaises(Exception):  # Should raise IntegrityError
            AgentFsNode.objects.create(
                filespace=self.filespace,
                parent=parent,
                node_type=AgentFsNode.NodeType.FILE,
                name="document.txt",
                content=ContentFile(b"content2", name="document.txt")
            )


class AgentFileSpaceSignalTests(TestCase):
    """Test suite for AgentFileSpace signal handlers."""

    @classmethod
    def setUpTestData(cls):
        """Set up non-modified objects used by all test methods."""
        User = get_user_model()
        cls.user = User.objects.create_user(
            username='testuser@example.com',
            email='testuser@example.com',
            password='password'
        )
        # UserQuota is created by a signal, but we can get it and increase the limit for tests.
        quota, _ = UserQuota.objects.get_or_create(user=cls.user)
        quota.agent_limit = 100
        quota.save()

    def test_default_filespace_created_on_agent_creation(self):
        """Test that a default filespace is created when a PersistentAgent is created."""
        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="test-browser-agent"
        )
        
        # Create PersistentAgent - signal should create default filespace
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="test-agent",
            charter="Test charter",
            browser_use_agent=browser_agent
        )
        
        # Check that a filespace was created
        filespaces = AgentFileSpace.objects.filter(owner_user=self.user)
        self.assertEqual(filespaces.count(), 1)
        
        filespace = filespaces.first()
        self.assertEqual(filespace.name, f"{agent.name} Files")
        self.assertEqual(filespace.owner_user, self.user)
        
        # Check that access was granted
        access = AgentFileSpaceAccess.objects.filter(
            filespace=filespace,
            agent=agent
        ).first()
        
        self.assertIsNotNone(access)
        self.assertEqual(access.role, AgentFileSpaceAccess.Role.OWNER)
        self.assertTrue(access.is_default)

    @patch('api.models.logger')
    def test_filespace_creation_error_handling(self, mock_logger):
        """Test that errors during filespace creation are handled gracefully."""
        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="test-browser-agent"
        )
        
        # Mock AgentFileSpace.objects.create to raise an exception
        with patch('api.models.AgentFileSpace.objects.create', side_effect=Exception("Database error")):
            # Creating agent should not fail even if filespace creation fails
            agent = PersistentAgent.objects.create(
                user=self.user,
                name="test-agent",
                charter="Test charter",
                browser_use_agent=browser_agent
            )
            
            # Check that error was logged
            mock_logger.error.assert_called_once()
            
            # Check that no filespace was created
            filespaces = AgentFileSpace.objects.filter(owner_user=self.user)
            self.assertEqual(filespaces.count(), 0)


class AgentFsNodeIntegrationTests(TestCase):
    """Integration tests for AgentFsNode with real file operations."""

    @classmethod
    def setUpTestData(cls):
        """Set up non-modified objects used by all test methods."""
        User = get_user_model()
        cls.user = User.objects.create_user(
            username='testuser@example.com',
            email='testuser@example.com',
            password='password'
        )

    def setUp(self):
        """Set up objects for each test method."""
        self.filespace = AgentFileSpace.objects.create(
            name="Test Workspace",
            owner_user=self.user
        )

    def test_file_upload_and_storage(self):
        """Test that files are properly uploaded and stored."""
        # Create a file with binary content
        binary_content = b"This is a test file with binary content.\x00\x01\x02\x03"
        content_file = ContentFile(binary_content, name="binary_test.dat")
        
        file_node = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.FILE,
            name="binary_test.dat",
            content=content_file,
            mime_type="application/octet-stream"
        )
        
        # Verify the file was stored
        self.assertIsNotNone(file_node.content)
        self.assertTrue(file_node.content.name)
        
        # Verify the content can be read back
        stored_content = file_node.content.read()
        self.assertEqual(stored_content, binary_content)
        
        # Verify size was calculated
        self.assertEqual(file_node.size_bytes, len(binary_content))

    def test_upload_path_generation(self):
        """Test that upload paths are generated correctly."""
        content_file = ContentFile(b"test content", name="test_file.txt")
        
        file_node = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.FILE,
            name="test_file.txt",
            content=content_file
        )
        
        # Check that the upload path follows the expected pattern
        expected_prefix = f"agent_fs/{self.filespace.id}/{file_node.id}/"
        self.assertTrue(file_node.content.name.startswith(expected_prefix))
        self.assertTrue(file_node.content.name.endswith("test_file.txt"))

    def test_large_directory_structure_performance(self):
        """Test performance with a moderately large directory structure."""
        # Create a directory structure with multiple levels and files
        root = AgentFsNode(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="root"
        )
        root.save()
        
        # Create multiple subdirectories
        subdirs = []
        for i in range(10):
            subdir = AgentFsNode(
                filespace=self.filespace,
                parent=root,
                node_type=AgentFsNode.NodeType.DIR,
                name=f"subdir_{i}"
            )
            subdir.save()
            subdirs.append(subdir)
            
            # Add some files to each subdirectory
            for j in range(5):
                AgentFsNode.objects.create(
                    filespace=self.filespace,
                    parent=subdir,
                    node_type=AgentFsNode.NodeType.FILE,
                    name=f"file_{j}.txt",
                    content=ContentFile(f"Content for file {j}".encode(), name=f"file_{j}.txt")
                )
        
        # Verify the structure was created correctly
        self.assertEqual(AgentFsNode.objects.filter(filespace=self.filespace).count(), 61)  # 1 root + 10 subdirs + 50 files
        self.assertEqual(AgentFsNode.objects.directories().filter(filespace=self.filespace).count(), 11)
        self.assertEqual(AgentFsNode.objects.files().filter(filespace=self.filespace).count(), 50)
        
        # Test efficient querying
        root_children = AgentFsNode.objects.in_dir(root)
        self.assertEqual(root_children.count(), 10)
        
        # Test path computation works correctly for nested items
        first_subdir = subdirs[0]
        subdir_files = AgentFsNode.objects.in_dir(first_subdir)
        for file_node in subdir_files:
            expected_path = f"/root/subdir_0/{file_node.name}"
            self.assertEqual(file_node.path, expected_path)


class AgentFsNodeIndexPerformanceTests(TestCase):
    """Test suite for AgentFsNode index performance, specifically testing parent-only index."""

    @classmethod
    def setUpTestData(cls):
        """Set up non-modified objects used by all test methods."""
        User = get_user_model()
        cls.user = User.objects.create_user(
            username='testuser@example.com',
            email='testuser@example.com',
            password='password'
        )

    def setUp(self):
        """Set up objects for each test method."""
        self.filespace = AgentFileSpace.objects.create(
            name="Performance Test Workspace",
            owner_user=self.user
        )

    def test_in_dir_query_uses_parent_index(self):
        """Test that in_dir() queries can efficiently use the parent-only index."""
        from django.db import connection
        from django.test.utils import override_settings, CaptureQueriesContext

        # Create a parent directory
        parent_dir = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="parent"
        )

        # Create multiple children
        for i in range(5):
            AgentFsNode.objects.create(
                filespace=self.filespace,
                parent=parent_dir,
                node_type=AgentFsNode.NodeType.FILE,
                name=f"file_{i}.txt",
                content=ContentFile(f"Content {i}".encode(), name=f"file_{i}.txt")
            )

        # Test the in_dir() query (allow small ORM plan variation)
        with CaptureQueriesContext(connection) as ctx:
            children = list(AgentFsNode.objects.in_dir(parent_dir))
            self.assertEqual(len(children), 5)
        self.assertLessEqual(len(ctx.captured_queries), 2)

        # Verify the query plan would use the parent index
        # Note: We can't easily test the actual query plan in unit tests without 
        # database-specific code, but we can verify the query works efficiently
        
        # Test with None parent (root level)
        root_nodes = list(AgentFsNode.objects.in_dir(None))
        self.assertGreaterEqual(len(root_nodes), 1)  # At least the parent_dir

    def test_in_dir_query_with_large_dataset(self):
        """Test in_dir() performance with a larger dataset to ensure index effectiveness."""
        # Create multiple top-level directories
        parent_dirs = []
        for i in range(3):
            parent_dir = AgentFsNode.objects.create(
                filespace=self.filespace,
                node_type=AgentFsNode.NodeType.DIR,
                name=f"parent_{i}"
            )
            parent_dirs.append(parent_dir)

            # Create many children in each directory
            for j in range(20):
                AgentFsNode.objects.create(
                    filespace=self.filespace,
                    parent=parent_dir,
                    node_type=AgentFsNode.NodeType.FILE,
                    name=f"file_{j}.txt",
                    content=ContentFile(f"Content {i}-{j}".encode(), name=f"file_{j}.txt")
                )

        # Test that querying children of one specific parent is efficient
        # The parent-only index should make this fast regardless of total dataset size
        test_parent = parent_dirs[1]
        
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as ctx:
            children = list(AgentFsNode.objects.in_dir(test_parent))
            self.assertEqual(len(children), 20)
        self.assertLessEqual(len(ctx.captured_queries), 2)
            
        # Verify all children belong to the correct parent (separate query to avoid N+1)
        for child in children:
            self.assertEqual(child.parent_id, test_parent.id)

    def test_in_dir_combined_with_other_filters(self):
        """Test in_dir() combined with other QuerySet methods for performance."""
        # Create a directory with mixed content
        parent_dir = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="mixed_parent"
        )

        # Add subdirectories
        for i in range(3):
            AgentFsNode.objects.create(
                filespace=self.filespace,
                parent=parent_dir,
                node_type=AgentFsNode.NodeType.DIR,
                name=f"subdir_{i}"
            )

        # Add files
        for i in range(5):
            AgentFsNode.objects.create(
                filespace=self.filespace,
                parent=parent_dir,
                node_type=AgentFsNode.NodeType.FILE,
                name=f"file_{i}.txt",
                content=ContentFile(f"Content {i}".encode(), name=f"file_{i}.txt")
            )

        # Test combined queries
        # Files only in this directory
        files_in_dir = AgentFsNode.objects.in_dir(parent_dir).files()
        self.assertEqual(files_in_dir.count(), 5)

        # Directories only in this directory  
        dirs_in_dir = AgentFsNode.objects.in_dir(parent_dir).directories()
        self.assertEqual(dirs_in_dir.count(), 3)

        # Alive nodes in this directory (should be all since none are deleted)
        alive_in_dir = AgentFsNode.objects.in_dir(parent_dir).alive()
        self.assertEqual(alive_in_dir.count(), 8)  # 3 dirs + 5 files

    def test_in_dir_with_soft_deleted_nodes(self):
        """Test in_dir() behavior with soft-deleted nodes and index performance."""
        parent_dir = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="deletion_test_parent"
        )

        # Create some files
        files = []
        for i in range(5):
            file_node = AgentFsNode.objects.create(
                filespace=self.filespace,
                parent=parent_dir,
                node_type=AgentFsNode.NodeType.FILE,
                name=f"file_{i}.txt",
                content=ContentFile(f"Content {i}".encode(), name=f"file_{i}.txt")
            )
            files.append(file_node)

        # Soft delete some files
        files[1].is_deleted = True
        files[1].deleted_at = timezone.now()
        files[1].save()

        files[3].is_deleted = True
        files[3].deleted_at = timezone.now()
        files[3].save()

        # Test that in_dir() returns all nodes (including deleted)
        all_children = list(AgentFsNode.objects.in_dir(parent_dir))
        self.assertEqual(len(all_children), 5)

        # Test that alive() filter works correctly with in_dir()
        alive_children = list(AgentFsNode.objects.in_dir(parent_dir).alive())
        self.assertEqual(len(alive_children), 3)

        # Verify the deleted nodes are not in alive results
        alive_names = {node.name for node in alive_children}
        self.assertNotIn("file_1.txt", alive_names)
        self.assertNotIn("file_3.txt", alive_names)

    def test_parent_index_covers_null_parent_queries(self):
        """Test that the parent index efficiently handles NULL parent queries for root nodes."""
        # Create some root-level nodes
        for i in range(5):
            AgentFsNode.objects.create(
                filespace=self.filespace,
                node_type=AgentFsNode.NodeType.DIR,
                name=f"root_dir_{i}"
            )

        # Create some nested nodes
        parent = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="parent_with_children"
        )
        
        for i in range(3):
            AgentFsNode.objects.create(
                filespace=self.filespace,
                parent=parent,
                node_type=AgentFsNode.NodeType.FILE,
                name=f"child_{i}.txt",
                content=ContentFile(f"Content {i}".encode(), name=f"child_{i}.txt")
            )

        # Query root level nodes (parent IS NULL)
        root_nodes = list(AgentFsNode.objects.in_dir(None))
        
        # Should find the 5 root dirs + 1 parent dir = 6 total
        self.assertEqual(len(root_nodes), 6)
        
        # Verify all returned nodes have no parent
        for node in root_nodes:
            self.assertIsNone(node.parent)

        # Verify nested nodes are not included
        nested_nodes = list(AgentFsNode.objects.in_dir(parent))
        self.assertEqual(len(nested_nodes), 3)
        
        for node in nested_nodes:
            self.assertEqual(node.parent_id, parent.id)


class AgentFsNodeSoftDeleteTests(TestCase):
    """Test suite for the AgentFsNode soft-delete semantics."""

    @classmethod
    def setUpTestData(cls):
        """Set up non-modified objects used by all test methods."""
        User = get_user_model()
        cls.user = User.objects.create_user(
            username='testuser@example.com',
            email='testuser@example.com',
            password='password'
        )
        # UserQuota is created by a signal, but we can get it and increase the limit for tests.
        quota, _ = UserQuota.objects.get_or_create(user=cls.user)
        quota.agent_limit = 100
        quota.save()

    def setUp(self):
        """Set up objects for each test method."""
        self.filespace = AgentFileSpace.objects.create(
            name="Test Workspace",
            owner_user=self.user
        )

    def test_subtree_deletion_basic(self):
        """Test that deleting a directory automatically deletes all descendants."""
        # Create directory structure: /parent/child/grandchild.txt
        parent = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="parent"
        )
        
        child = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=parent,
            node_type=AgentFsNode.NodeType.DIR,
            name="child"
        )
        
        grandchild = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=child,
            node_type=AgentFsNode.NodeType.FILE,
            name="grandchild.txt",
            content=ContentFile(b"content", name="grandchild.txt")
        )
        
        # Initially all nodes are alive
        self.assertFalse(parent.is_deleted)
        self.assertFalse(child.is_deleted)
        self.assertFalse(grandchild.is_deleted)
        
        # Delete the parent directory
        parent.is_deleted = True
        parent.save()
        
        # Refresh from database
        child.refresh_from_db()
        grandchild.refresh_from_db()
        
        # All descendants should now be deleted
        self.assertTrue(parent.is_deleted)
        self.assertTrue(child.is_deleted)
        self.assertTrue(grandchild.is_deleted)
        
        # All should have deletion timestamps
        self.assertIsNotNone(parent.deleted_at)
        self.assertIsNotNone(child.deleted_at)
        self.assertIsNotNone(grandchild.deleted_at)

    def test_subtree_deletion_mixed_structure(self):
        """Test subtree deletion with mixed files and directories."""
        # Create structure:
        # /root
        #   /docs
        #     readme.txt
        #     license.txt
        #   /src
        #     /utils
        #       helper.py
        #     main.py
        #   config.json
        
        root = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="root"
        )
        
        docs = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=root,
            node_type=AgentFsNode.NodeType.DIR,
            name="docs"
        )
        
        readme = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=docs,
            node_type=AgentFsNode.NodeType.FILE,
            name="readme.txt",
            content=ContentFile(b"readme content", name="readme.txt")
        )
        
        license_file = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=docs,
            node_type=AgentFsNode.NodeType.FILE,
            name="license.txt",
            content=ContentFile(b"license content", name="license.txt")
        )
        
        src = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=root,
            node_type=AgentFsNode.NodeType.DIR,
            name="src"
        )
        
        utils = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=src,
            node_type=AgentFsNode.NodeType.DIR,
            name="utils"
        )
        
        helper = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=utils,
            node_type=AgentFsNode.NodeType.FILE,
            name="helper.py",
            content=ContentFile(b"helper code", name="helper.py")
        )
        
        main = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=src,
            node_type=AgentFsNode.NodeType.FILE,
            name="main.py",
            content=ContentFile(b"main code", name="main.py")
        )
        
        config = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=root,
            node_type=AgentFsNode.NodeType.FILE,
            name="config.json",
            content=ContentFile(b'{"config": true}', name="config.json")
        )
        
        # Delete the src directory only
        src.is_deleted = True
        src.save()
        
        # Refresh from database
        for node in [docs, readme, license_file, utils, helper, main, config]:
            node.refresh_from_db()
        
        # Only src and its descendants should be deleted
        self.assertFalse(root.is_deleted)
        self.assertFalse(docs.is_deleted)
        self.assertFalse(readme.is_deleted)
        self.assertFalse(license_file.is_deleted)
        self.assertFalse(config.is_deleted)
        
        # src and its descendants should be deleted
        self.assertTrue(src.is_deleted)
        self.assertTrue(utils.is_deleted)
        self.assertTrue(helper.is_deleted)
        self.assertTrue(main.is_deleted)

    def test_subtree_deletion_file_nodes(self):
        """Test that deleting a file doesn't affect other nodes."""
        parent = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="parent"
        )
        
        file1 = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=parent,
            node_type=AgentFsNode.NodeType.FILE,
            name="file1.txt",
            content=ContentFile(b"content1", name="file1.txt")
        )
        
        file2 = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=parent,
            node_type=AgentFsNode.NodeType.FILE,
            name="file2.txt",
            content=ContentFile(b"content2", name="file2.txt")
        )
        
        # Delete one file
        file1.is_deleted = True
        file1.save()
        
        # Refresh from database
        parent.refresh_from_db()
        file2.refresh_from_db()
        
        # Only the deleted file should be affected
        self.assertFalse(parent.is_deleted)
        self.assertFalse(file2.is_deleted)
        self.assertTrue(file1.is_deleted)

    def test_trash_subtree_helper_method(self):
        """Test the trash_subtree() helper method."""
        # Create structure: /app/src/utils/helper.js
        app = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="app"
        )
        
        src = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=app,
            node_type=AgentFsNode.NodeType.DIR,
            name="src"
        )
        
        utils = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=src,
            node_type=AgentFsNode.NodeType.DIR,
            name="utils"
        )
        
        helper = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=utils,
            node_type=AgentFsNode.NodeType.FILE,
            name="helper.js",
            content=ContentFile(b"helper code", name="helper.js")
        )
        
        # Use trash_subtree() method
        deleted_count = app.trash_subtree()
        
        # Should return count of deleted nodes (4: app, src, utils, helper)
        self.assertEqual(deleted_count, 4)
        
        # Refresh from database
        for node in [app, src, utils, helper]:
            node.refresh_from_db()
        
        # All should be deleted
        self.assertTrue(app.is_deleted)
        self.assertTrue(src.is_deleted)
        self.assertTrue(utils.is_deleted)
        self.assertTrue(helper.is_deleted)
        
        # All should have deletion timestamps
        self.assertIsNotNone(app.deleted_at)
        self.assertIsNotNone(src.deleted_at)
        self.assertIsNotNone(utils.deleted_at)
        self.assertIsNotNone(helper.deleted_at)

    def test_trash_subtree_file_node(self):
        """Test trash_subtree() on a file node."""
        file_node = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.FILE,
            name="standalone.txt",
            content=ContentFile(b"content", name="standalone.txt")
        )
        
        # Use trash_subtree() on a file
        deleted_count = file_node.trash_subtree()
        
        # Should return 1 (only the file itself)
        self.assertEqual(deleted_count, 1)
        
        # File should be deleted
        file_node.refresh_from_db()
        self.assertTrue(file_node.is_deleted)
        self.assertIsNotNone(file_node.deleted_at)

    def test_restore_subtree_method(self):
        """Test the restore_subtree() helper method."""
        # Create and delete a directory structure
        parent = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="parent"
        )
        
        child = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=parent,
            node_type=AgentFsNode.NodeType.DIR,
            name="child"
        )
        
        file_node = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=child,
            node_type=AgentFsNode.NodeType.FILE,
            name="file.txt",
            content=ContentFile(b"content", name="file.txt")
        )
        
        # Delete the entire subtree
        parent.trash_subtree()
        
        # Verify all are deleted
        for node in [parent, child, file_node]:
            node.refresh_from_db()
            self.assertTrue(node.is_deleted)
        
        # Restore the subtree
        restored_count = parent.restore_subtree()
        
        # Should restore all 3 nodes
        self.assertEqual(restored_count, 3)
        
        # Verify all are restored
        for node in [parent, child, file_node]:
            node.refresh_from_db()
            self.assertFalse(node.is_deleted)
            self.assertIsNone(node.deleted_at)

    def test_get_descendants_method(self):
        """Test the get_descendants() helper method."""
        # Create directory structure
        root = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="root"
        )
        
        dir1 = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=root,
            node_type=AgentFsNode.NodeType.DIR,
            name="dir1"
        )
        
        dir2 = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=root,
            node_type=AgentFsNode.NodeType.DIR,
            name="dir2"
        )
        
        file1 = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=dir1,
            node_type=AgentFsNode.NodeType.FILE,
            name="file1.txt",
            content=ContentFile(b"content1", name="file1.txt")
        )
        
        file2 = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=dir2,
            node_type=AgentFsNode.NodeType.FILE,
            name="file2.txt",
            content=ContentFile(b"content2", name="file2.txt")
        )
        
        # Delete one file
        file1.is_deleted = True
        file1.save()
        
        # Get descendants excluding deleted
        descendants = root.get_descendants(include_deleted=False)
        descendant_names = {node.name for node in descendants}
        
        self.assertEqual(len(descendants), 3)  # dir1, dir2, file2
        self.assertIn("dir1", descendant_names)
        self.assertIn("dir2", descendant_names)
        self.assertIn("file2.txt", descendant_names)
        self.assertNotIn("file1.txt", descendant_names)
        
        # Get descendants including deleted
        all_descendants = root.get_descendants(include_deleted=True)
        all_names = {node.name for node in all_descendants}
        
        self.assertEqual(len(all_descendants), 4)  # dir1, dir2, file1, file2
        self.assertIn("file1.txt", all_names)

    def test_get_descendants_file_node(self):
        """Test get_descendants() on a file node returns empty queryset."""
        file_node = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.FILE,
            name="file.txt",
            content=ContentFile(b"content", name="file.txt")
        )
        
        descendants = file_node.get_descendants()
        self.assertEqual(descendants.count(), 0)

    def test_partially_deleted_hierarchy(self):
        """Test behavior when some nodes in a hierarchy are already deleted."""
        # Create structure
        parent = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="parent"
        )
        
        child1 = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=parent,
            node_type=AgentFsNode.NodeType.DIR,
            name="child1"
        )
        
        child2 = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=parent,
            node_type=AgentFsNode.NodeType.DIR,
            name="child2"
        )
        
        grandchild = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=child1,
            node_type=AgentFsNode.NodeType.FILE,
            name="grandchild.txt",
            content=ContentFile(b"content", name="grandchild.txt")
        )
        
        # Pre-delete child2
        child2.is_deleted = True
        child2.save()
        
        # Delete parent
        parent.is_deleted = True
        parent.save()
        
        # Refresh all from database
        for node in [child1, child2, grandchild]:
            node.refresh_from_db()
        
        # All should be deleted
        self.assertTrue(parent.is_deleted)
        self.assertTrue(child1.is_deleted)
        self.assertTrue(child2.is_deleted)  # Was already deleted
        self.assertTrue(grandchild.is_deleted)

    def test_deletion_timestamps_consistency(self):
        """Test that deletion timestamps are set consistently."""
        from django.utils import timezone
        
        before_deletion = timezone.now()
        
        # Create structure
        parent = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="parent"
        )
        
        child = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=parent,
            node_type=AgentFsNode.NodeType.FILE,
            name="child.txt",
            content=ContentFile(b"content", name="child.txt")
        )
        
        # Delete parent
        parent.is_deleted = True
        parent.save()
        
        after_deletion = timezone.now()
        
        # Refresh child
        child.refresh_from_db()
        
        # Both should have deletion timestamps in the expected range
        self.assertIsNotNone(parent.deleted_at)
        self.assertIsNotNone(child.deleted_at)
        self.assertGreaterEqual(parent.deleted_at, before_deletion)
        self.assertLessEqual(parent.deleted_at, after_deletion)
        self.assertGreaterEqual(child.deleted_at, before_deletion)
        self.assertLessEqual(child.deleted_at, after_deletion)

    def test_unique_constraints_with_deleted_nodes(self):
        """Test that unique constraints work correctly with deleted nodes."""
        # Create a directory
        dir1 = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="testdir"
        )
        
        # Delete it
        dir1.is_deleted = True
        dir1.save()
        
        # Should be able to create another directory with the same name
        dir2 = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="testdir"
        )
        
        self.assertEqual(dir1.name, dir2.name)
        self.assertTrue(dir1.is_deleted)
        self.assertFalse(dir2.is_deleted)

    def test_queryset_alive_filter_with_subtree_deletion(self):
        """Test that the alive() queryset filter works correctly after subtree deletion."""
        # Create structure
        parent = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="parent"
        )
        
        child = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=parent,
            node_type=AgentFsNode.NodeType.FILE,
            name="child.txt",
            content=ContentFile(b"content", name="child.txt")
        )
        
        other_node = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="other"
        )
        
        # Before deletion - all alive
        alive_nodes = AgentFsNode.objects.alive()
        self.assertEqual(alive_nodes.count(), 3)
        
        # Delete parent (triggers subtree deletion)
        parent.is_deleted = True
        parent.save()
        
        # After deletion - only other_node alive
        alive_nodes = AgentFsNode.objects.alive()
        self.assertEqual(alive_nodes.count(), 1)
        self.assertEqual(alive_nodes.first(), other_node)

    def test_rename_and_delete_in_same_save(self):
        """
        Test that renaming a directory and marking it as deleted in the same save
        operation properly deletes all descendants.
        
        This is a regression test for the bug where _propagate_deletion_to_descendants()
        ran before updating descendant paths, causing it to miss descendants that still
        had the old path.
        """
        # Create directory structure: /original_parent/child/grandchild.txt
        original_parent = AgentFsNode.objects.create(
            filespace=self.filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="original_parent"
        )
        
        child = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=original_parent,
            node_type=AgentFsNode.NodeType.DIR,
            name="child"
        )
        
        grandchild_file = AgentFsNode.objects.create(
            filespace=self.filespace,
            parent=child,
            node_type=AgentFsNode.NodeType.FILE,
            name="grandchild.txt",
            content=ContentFile(b"test content", name="grandchild.txt")
        )
        
        # Verify initial structure and paths
        original_parent.refresh_from_db()
        child.refresh_from_db()
        grandchild_file.refresh_from_db()
        
        self.assertEqual(original_parent.path, "/original_parent")
        self.assertEqual(child.path, "/original_parent/child")
        self.assertEqual(grandchild_file.path, "/original_parent/child/grandchild.txt")
        
        # All nodes should be alive initially
        self.assertFalse(original_parent.is_deleted)
        self.assertFalse(child.is_deleted)
        self.assertFalse(grandchild_file.is_deleted)
        
        # Now rename the parent AND mark it as deleted in the same operation
        # This simulates the problematic scenario where both path changes and deletion happen together
        original_parent.name = "renamed_parent"
        original_parent.is_deleted = True
        original_parent.save()
        
        # Refresh all nodes from database
        original_parent.refresh_from_db()
        child.refresh_from_db()
        grandchild_file.refresh_from_db()
        
        # The parent should be deleted and have the new path
        self.assertTrue(original_parent.is_deleted)
        self.assertEqual(original_parent.path, "/renamed_parent")
        
        # Most importantly: descendants should also be marked as deleted
        # This would fail if the bug was present - descendants would still be alive
        # because _propagate_deletion_to_descendants() would have run before path updates
        self.assertTrue(child.is_deleted, "Child directory should be marked as deleted")
        self.assertTrue(grandchild_file.is_deleted, "Grandchild file should be marked as deleted")
        
        # Descendants should also have updated paths
        self.assertEqual(child.path, "/renamed_parent/child")
        self.assertEqual(grandchild_file.path, "/renamed_parent/child/grandchild.txt")
        
        # Verify that all nodes have deleted_at timestamps
        self.assertIsNotNone(original_parent.deleted_at)
        self.assertIsNotNone(child.deleted_at)
        self.assertIsNotNone(grandchild_file.deleted_at)
        
        # Verify the alive() manager correctly excludes all these nodes
        alive_nodes = AgentFsNode.objects.alive().filter(filespace=self.filespace)
        self.assertEqual(alive_nodes.count(), 0)
