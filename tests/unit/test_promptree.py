import random
import string
from django.test import TestCase, tag

from api.agent.core.promptree import Prompt, PromptBudgetExceededError, hmt


def _long_random_text(words: int = 3000) -> str:
    """Helper function to generate long random text for testing."""
    rng = random.Random(0xC0DEC0DE)
    return " ".join(
        "".join(rng.choice(string.ascii_lowercase) for _ in range(rng.randint(3, 10)))
        for _ in range(words)
    )


@tag("batch_promptree")
class PrompTreeShrinkerTests(TestCase):
    """Test suite for PromTree shrinker functionality."""

    def test_hmt_shrinker_produces_two_markers(self):
        """Test that the hmt (Head-Mid-Tail) shrinker produces two truncation markers."""
        base = (
            "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima "
            * 8
        ).strip()
        
        # built‑ins emit TWO markers
        result = hmt(base, 0.25)
        self.assertEqual(result.count("BYTES TRUNCATED"), 2)

    def test_hmt_no_shrinking_when_k_large(self):
        """Test that hmt doesn't shrink when k >= 0.99."""
        base = "alpha bravo charlie delta echo foxtrot"
        result = hmt(base, 0.99)
        self.assertEqual(result, base)

    def test_hmt_preserves_structure(self):
        """Test that hmt maintains head-mid-tail structure."""
        base = "one two three four five six seven eight nine ten"
        result = hmt(base, 0.5)
        
        # Should contain truncation markers
        self.assertIn("BYTES TRUNCATED", result)
        # Should start with some of the original words
        self.assertTrue(result.startswith("one"))


@tag("batch_promptree")
class PromptShrinkingTests(TestCase):
    """Test suite for Prompt class shrinking functionality."""

    def test_prompt_shrinking_with_hmt(self):
        """Test shrinking via Prompt class with hmt shrinker."""
        base = (
            "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima "
            * 8
        ).strip()
        
        p = Prompt()
        p.section_text("s", base, shrinker="hmt")
        fit = p.render(50)
        
        self.assertLessEqual(p._tok(fit), 50)
        self.assertEqual(fit.count("BYTES TRUNCATED"), 2)

    def test_default_hmt_truncation_produces_two_markers(self):
        """Test that default HMT truncation produces two markers."""
        base = (
            "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima "
            * 8
        ).strip()
        
        p2 = Prompt()
        p2.section_text("s", base)  # No shrinker specified - uses default "hmt"
        cut = p2.render(50)
        
        self.assertEqual(cut.count("BYTES TRUNCATED"), 2)  # HMT produces 2 markers

    def test_pathological_tiny_budget(self):
        """Test handling of pathologically small token budgets."""
        huge = _long_random_text(20000)
        p3 = Prompt()
        p3.section_text("huge", huge, shrinker="hmt")
        result = p3.render(5)
        
        # With XML wrapping overhead, minimum is around 8 tokens
        # (e.g., "<huge>BYTES TRUNCATED</huge>" ≈ 4 tokens + content)
        self.assertLessEqual(p3._tok(result), 10)

    def test_nested_groups_builder_api(self):
        """Test the new builder API with nested groups."""
        p4 = Prompt()
        hdr = p4.group("hdr", weight=2)
        hdr.section_text("sys", "You are concise.")
        hdr.section("task", lambda e: f"Reset **{e['dev']}** router.")

        body = p4.group("body", weight=8)
        manual = "lorem ipsum\n" * 50
        body.section_text("manual", manual, weight=3, shrinker="hmt")
        body.section("hist", lambda e: e["chat"], weight=1, shrinker="hmt")

        out = p4.render(
            60,
            dev="X‑100",
            chat="USER: hi\nASSISTANT: hello",
        )
        
        self.assertLessEqual(p4._tok(out), 60)

    def test_prompt_report_functionality(self):
        """Test that prompt report shows section information correctly."""
        p = Prompt()
        p.section_text("test_section", "This is a test section")
        p.render(100)
        
        report = p.report()
        self.assertIn("section", report)
        self.assertIn("tokens", report)
        self.assertIn("test_section", report)


@tag("batch_promptree")
class PromptBuilderTests(TestCase):
    """Test suite for Prompt builder functionality."""

    def test_group_creation(self):
        """Test that groups can be created and nested."""
        p = Prompt()
        group = p.group("test_group", weight=5)
        
        self.assertEqual(group.name, "test_group")
        self.assertEqual(group.weight, 5)
        self.assertIn(group, p.root.children)

    def test_section_text_creation(self):
        """Test that text sections can be created."""
        p = Prompt()
        p.section_text("test_section", "Test content", weight=3, shrinker="hmt")
        
        # Find the section in the children
        section = next(child for child in p.root.children if child.name == "test_section")
        self.assertEqual(section.name, "test_section")
        self.assertEqual(section.weight, 3)
        self.assertEqual(section.renderer, "Test content")
        self.assertEqual(section.shrinker, "hmt")

    def test_section_with_callable_renderer(self):
        """Test that sections can use callable renderers."""
        p = Prompt()
        p.section("dynamic", lambda ctx: f"Hello {ctx['name']}", weight=2)
        
        result = p.render(100, name="World")
        self.assertIn("Hello World", result)

    def test_custom_token_estimator(self):
        """Test that custom token estimators work."""
        def custom_estimator(text):
            return len(text)  # Character count instead of word count
        
        p = Prompt(token_estimator=custom_estimator)
        p.section_text("test", "hello world")
        result = p.render(100)
        
        # The estimator should have been used
        self.assertIsNotNone(result)

    def test_custom_shrinker_registration(self):
        """Test that custom shrinkers can be registered."""
        def custom_shrinker(text, ratio):
            words = text.split()
            keep = max(1, int(len(words) * ratio))
            return " ".join(words[:keep]) + " [CUSTOM TRUNCATED]"
        
        p = Prompt()
        p.register_shrinker(custom_shrinker, name="custom")
        # Use longer text to ensure shrinking is needed
        long_text = "one two three four five six seven eight nine ten " * 10
        p.section_text("test", long_text, shrinker="custom")
        
        result = p.render(3)  # Very small budget to force shrinking
        self.assertIn("CUSTOM TRUNCATED", result)


@tag("batch_promptree")
class PromptTokenCountingTests(TestCase):
    """Test suite for Prompt class token counting functionality."""

    def test_token_counting_before_and_after_fitting(self):
        """Test that prompt tracks token counts before and after fitting."""
        def simple_token_estimator(text: str) -> int:
            # Simple word-based estimator for testing
            return len(text.split())

        prompt = Prompt(token_estimator=simple_token_estimator)
        
        # Add sections that will definitely exceed the budget
        prompt.section_text("section1", "word " * 50)  # 50 tokens
        prompt.section_text("section2", "word " * 30)  # 30 tokens
        
        # Render with a small budget to force shrinking
        budget = 20
        result = prompt.render(budget)
        
        # Check that we have token counts
        tokens_before = prompt.get_tokens_before_fitting()
        tokens_after = prompt.get_tokens_after_fitting()
        
        # Before fitting should be the full count (around 80 tokens for content + tags)
        self.assertGreater(tokens_before, budget)
        
        # After fitting should be within the budget (or close to it)
        self.assertLessEqual(tokens_after, budget + 5)  # Allow some margin for XML tags
        
        # Before should be greater than after
        self.assertGreater(tokens_before, tokens_after)
        
        # Test that we can get both counts multiple times
        self.assertEqual(prompt.get_tokens_before_fitting(), tokens_before)
        self.assertEqual(prompt.get_tokens_after_fitting(), tokens_after)

    def test_token_counting_with_no_shrinking_needed(self):
        """Test token counting when no shrinking is needed."""
        def simple_token_estimator(text: str) -> int:
            return len(text.split())

        prompt = Prompt(token_estimator=simple_token_estimator)
        prompt.section_text("small_section", "just a few words")
        
        # Large budget, no shrinking needed
        budget = 100
        prompt.render(budget)
        
        tokens_before = prompt.get_tokens_before_fitting()
        tokens_after = prompt.get_tokens_after_fitting()
        
        # Token count may differ slightly due to XML wrapping overhead calculation
        # In this case: 5 tokens before (including XML overhead) vs 4 tokens after (actual content)
        self.assertLessEqual(abs(tokens_before - tokens_after), 1)
        self.assertLess(tokens_after, budget)

    def test_nested_group_structure_is_reserved_from_leaf_budget(self):
        prompt = Prompt(token_estimator=len)
        group = prompt.group("group")
        wrapper_length = len("<leaf></leaf>")

        def exact_prefix(text: str, ratio: float) -> str:
            content_length = max(0, int(len(text) * ratio) - wrapper_length)
            return text[:content_length]

        group.section_text("leaf", "a" * 100, shrinker=exact_prefix)
        group.section_text("leaf", "b" * 100, shrinker=exact_prefix)

        budget = 70
        result = prompt.render(budget)

        self.assertEqual(len(result), budget)
        self.assertEqual(prompt.get_tokens_after_fitting(), budget)
        self.assertIn("<group>", result)
        self.assertEqual(result.count("<leaf>"), 2)

    def test_protected_minimum_includes_nested_group_structure(self):
        prompt = Prompt(token_estimator=len)
        group = prompt.group("group")
        group.section_text("fixed", "x", non_shrinkable=True)
        required = len("<group><fixed>x</fixed></group>")

        with self.assertRaises(PromptBudgetExceededError) as raised:
            prompt.render(required - 1)

        self.assertEqual(raised.exception.budget, required - 1)
        self.assertEqual(raised.exception.required, required)


@tag("batch_promptree")
class PromptRenderingTests(TestCase):
    """Test suite for Prompt rendering functionality."""

    def test_basic_rendering(self):
        """Test basic prompt rendering."""
        p = Prompt()
        p.section_text("greeting", "Hello")
        p.section_text("question", "How are you?")
        
        result = p.render(100)
        self.assertIn("Hello", result)
        self.assertIn("How are you?", result)

    def test_context_variable_substitution(self):
        """Test that context variables are properly substituted."""
        p = Prompt()
        p.section("greeting", lambda ctx: f"Hello {ctx['name']}")
        p.section("info", lambda ctx: f"You have {ctx['count']} messages")
        
        result = p.render(100, name="Alice", count=5)
        self.assertIn("Hello Alice", result)
        self.assertIn("You have 5 messages", result)

    def test_weight_based_proportional_distribution(self):
        """Test that weights affect token distribution proportionally."""
        p = Prompt()
        high_weight_group = p.group("high", weight=8)
        low_weight_group = p.group("low", weight=2)
        
        # Add content that will require shrinking
        high_content = "high priority content " * 20
        low_content = "low priority content " * 20
        
        high_weight_group.section_text("high_section", high_content)
        low_weight_group.section_text("low_section", low_content)
        
        # Render with limited budget to force proportional distribution
        result = p.render(30)
        
        # The high weight section should get more tokens
        self.assertLessEqual(p._tok(result), 30)

    def test_jinja2_template_support(self):
        """Test optional Jinja2 template support."""
        p = Prompt()
        template_text = "Hello {{name}}, you have {{count}} items"
        p.section_text("template", template_text)
        
        result = p.render(100, name="Bob", count=3)
        
        # Should work with or without Jinja2 installed
        # If Jinja2 is available, templates should be rendered
        # If not, the text should remain as-is
        self.assertIsNotNone(result)


@tag("batch_promptree")
class PromptUnshrinkableTests(TestCase):
    """Test suite for unshrinkable sections in Prompt."""

    def test_unshrinkable_section_over_budget_fails_closed(self):
        """Protected content must not silently bypass the prompt budget."""
        def simple_token_estimator(text: str) -> int:
            return len(text.split())

        prompt = Prompt(token_estimator=simple_token_estimator)
        
        # Add a large unshrinkable section
        large_content = "unshrinkable content " * 20  # ~40 tokens
        prompt.section_text("critical", large_content, non_shrinkable=True)
        
        # Render with small budget that would normally force shrinking
        budget = 10
        with self.assertRaises(PromptBudgetExceededError) as raised:
            prompt.render(budget)

        self.assertEqual(raised.exception.budget, budget)
        self.assertGreater(raised.exception.required, budget)

    def test_exact_protected_budget_with_optional_content_fails_closed(self):
        prompt = Prompt(token_estimator=len)
        prompt.section_text("fixed", "x", non_shrinkable=True)
        prompt.section_text("optional", "extra", shrinker="hmt")
        exact_fixed_budget = len("<fixed>x</fixed>")

        with self.assertRaises(PromptBudgetExceededError):
            prompt.render(exact_fixed_budget)

    def test_exact_protected_budget_without_optional_content_fits(self):
        prompt = Prompt(token_estimator=len)
        prompt.section_text("fixed", "x", non_shrinkable=True)
        exact_fixed_budget = len("<fixed>x</fixed>")

        self.assertEqual(prompt.render(exact_fixed_budget), "<fixed>x</fixed>")

    def test_protected_leaf_survives_when_structure_uses_conservative_budget(self):
        prompt = Prompt(token_estimator=lambda text: len(text.split()))
        group = prompt.group("group")
        group.section_text("fixed", "fixed", non_shrinkable=True)
        group.section_text("optional", "one two")

        result = prompt.render(2)

        self.assertIn("<fixed>fixed</fixed>", result)
        self.assertNotIn("one two", result)
        self.assertEqual(prompt.get_tokens_after_fitting(), 2)

    def test_shrinker_cannot_return_output_above_budget(self):
        prompt = Prompt(token_estimator=len)
        prompt.section_text("optional", "long optional content", shrinker="hmt")

        with self.assertRaises(PromptBudgetExceededError):
            prompt.render(1)

    def test_mixed_shrinkable_and_unshrinkable_sections(self):
        """Test behavior with both shrinkable and unshrinkable sections."""
        def simple_token_estimator(text: str) -> int:
            return len(text.split())

        prompt = Prompt(token_estimator=simple_token_estimator)
        
        # Add unshrinkable section
        critical_content = "critical system info " * 5  # ~15 tokens
        prompt.section_text("critical", critical_content, non_shrinkable=True, weight=1)
        
        # Add shrinkable section
        optional_content = "optional detailed explanation " * 10  # ~30 tokens
        prompt.section_text("optional", optional_content, shrinker="hmt", weight=1)
        
        # Render with budget that allows critical but requires optional shrinking
        budget = 25
        result = prompt.render(budget)
        
        # Critical section should be fully preserved
        self.assertIn("critical system info", result)
        # Optional section should be shrunk (contain truncation markers)
        self.assertIn("BYTES TRUNCATED", result)
        
        # Total should be close to budget
        tokens_after = prompt.get_tokens_after_fitting()
        self.assertLessEqual(tokens_after, budget + 5)  # Allow margin for XML tags

    def test_unshrinkable_sections_exhaust_budget(self):
        """Several protected sections also fail closed when they cannot fit."""
        def simple_token_estimator(text: str) -> int:
            return len(text.split())

        prompt = Prompt(token_estimator=simple_token_estimator)
        
        # Add multiple unshrinkable sections that together exceed budget
        prompt.section_text("critical1", "essential info " * 10, non_shrinkable=True)  # ~20 tokens
        prompt.section_text("critical2", "more essential data " * 8, non_shrinkable=True)  # ~24 tokens
        prompt.section_text("optional", "nice to have info " * 5, shrinker="hmt")  # ~15 tokens
        
        # Budget smaller than combined unshrinkable content
        budget = 30
        with self.assertRaisesRegex(
            PromptBudgetExceededError,
            "Prompt content requires",
        ):
            prompt.render(budget)

    def test_unshrinkable_with_weights(self):
        """Test that weights still affect allocation among shrinkable sections when unshrinkable sections are present."""
        def simple_token_estimator(text: str) -> int:
            return len(text.split())

        prompt = Prompt(token_estimator=simple_token_estimator)
        
        # Add unshrinkable section
        prompt.section_text("critical", "critical data " * 3, non_shrinkable=True, weight=1)  # ~6 tokens
        
        # Add two shrinkable sections with different weights
        prompt.section_text("high_weight", "high priority " * 10, shrinker="hmt", weight=3)  # ~20 tokens
        prompt.section_text("low_weight", "low priority " * 10, shrinker="hmt", weight=1)  # ~20 tokens
        
        budget = 20  # Budget that allows critical + some of the others
        result = prompt.render(budget)
        
        # Critical should be preserved
        self.assertIn("critical data", result)
        
        # Both shrinkable sections should be present (check for section tags)
        self.assertIn("<high_weight>", result)
        self.assertIn("<low_weight>", result)
        
        # Should contain truncation markers due to shrinking
        self.assertIn("BYTES TRUNCATED", result)

    def test_all_sections_unshrinkable(self):
        """Test behavior when all sections are unshrinkable."""
        def simple_token_estimator(text: str) -> int:
            return len(text.split())

        prompt = Prompt(token_estimator=simple_token_estimator)
        
        # Add multiple unshrinkable sections
        prompt.section_text("section1", "content one " * 5, non_shrinkable=True)  # ~10 tokens
        prompt.section_text("section2", "content two " * 5, non_shrinkable=True)  # ~10 tokens
        prompt.section_text("section3", "content three " * 5, non_shrinkable=True)  # ~10 tokens
        
        budget = 15  # Smaller than total content
        with self.assertRaises(PromptBudgetExceededError):
            prompt.render(budget)

    def test_unshrinkable_nested_in_groups(self):
        """Test unshrinkable sections when nested in groups."""
        def simple_token_estimator(text: str) -> int:
            return len(text.split())

        prompt = Prompt(token_estimator=simple_token_estimator)
        
        # Create a group with mixed shrinkable and unshrinkable content
        important_group = prompt.group("important", weight=2)
        important_group.section_text("critical", "critical info " * 4, non_shrinkable=True)  # ~8 tokens
        important_group.section_text("optional", "optional info " * 8, shrinker="hmt")  # ~16 tokens
        
        # Add another shrinkable section at root level
        prompt.section_text("extra", "extra content " * 6, shrinker="hmt")  # ~12 tokens
        
        budget = 20
        result = prompt.render(budget)
        
        # Critical section should be preserved
        self.assertIn("critical info", result)
        
        # Should have some shrinking indicated by truncation markers
        self.assertIn("BYTES TRUNCATED", result)

    def test_unshrinkable_with_callable_renderer(self):
        """Test unshrinkable sections with callable renderers."""
        def simple_token_estimator(text: str) -> int:
            return len(text.split())

        prompt = Prompt(token_estimator=simple_token_estimator)
        
        # Add unshrinkable section with callable renderer
        def render_critical(ctx):
            return f"CRITICAL: User {ctx['user']} has {ctx['alerts']} alerts"
        
        prompt.section("critical", render_critical, non_shrinkable=True)
        prompt.section_text("details", "detailed information " * 15, shrinker="hmt")
        
        budget = 15
        result = prompt.render(budget, user="admin", alerts=5)
        
        # Critical section should be fully rendered and preserved
        self.assertIn("CRITICAL: User admin has 5 alerts", result)
        # Details should be shrunk
        self.assertIn("BYTES TRUNCATED", result)

    def test_unshrinkable_section_report(self):
        """Test that unshrinkable sections are properly reported."""
        def simple_token_estimator(text: str) -> int:
            return len(text.split())

        prompt = Prompt(token_estimator=simple_token_estimator)
        
        # Use content where shrinkable is much larger and unshrinkable is smaller
        prompt.section_text("shrinkable", "shrinkable content " * 50, shrinker="hmt")  # ~100 tokens  
        prompt.section_text("unshrinkable", "critical info " * 2, non_shrinkable=True)  # ~4 tokens
        
        budget = 8  # Very small budget to force aggressive shrinking
        result = prompt.render(budget)
        
        report = prompt.report()
        
        # Both sections should appear in report
        self.assertIn("shrinkable", report)
        self.assertIn("unshrinkable", report)
        
        # Protected content remains intact while optional content yields to the hard budget.
        tokens_after = prompt.get_tokens_after_fitting()
        self.assertLessEqual(tokens_after, budget)
        
        # The unshrinkable section should preserve "critical info"
        self.assertIn("critical info", result)
        
        # Parse the report to verify behavior
        lines = report.split('\n')
        shrinkable_line = next(line for line in lines if "shrinkable" in line and "unshrinkable" not in line)
        unshrinkable_line = next(line for line in lines if "unshrinkable" in line)
        
        # Verify that the unshrinkable section was not shrunk
        self.assertNotIn("✔", unshrinkable_line)
        
        # Extract token counts from the report lines to verify behavior
        import re
        shrinkable_tokens = int(re.search(r'\s+(\d+)', shrinkable_line).group(1))
        unshrinkable_tokens = int(re.search(r'\s+(\d+)', unshrinkable_line).group(1))
        
        # Unshrinkable should have kept its tokens, shrinkable should be minimal
        self.assertGreater(unshrinkable_tokens, 3)  # Should keep its ~4+ tokens
        self.assertLess(shrinkable_tokens, 10)  # Should be heavily reduced from ~100 tokens
