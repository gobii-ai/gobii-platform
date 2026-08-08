import json

from django.test import SimpleTestCase, tag

from api.services.discord_embeds import (
    DISCORD_EMBED_DESCRIPTION_MAX_LENGTH,
    DISCORD_EMBED_FIELD_NAME_MAX_LENGTH,
    DISCORD_EMBED_FIELD_VALUE_MAX_LENGTH,
    DISCORD_EMBED_TITLE_MAX_LENGTH,
    DISCORD_EMBED_TOTAL_TEXT_MAX_LENGTH,
    DISCORD_MAX_EMBED_FIELDS,
    DISCORD_MAX_EMBEDS,
    discord_embed_signature_projection,
    discord_embed_tool_schema,
    format_discord_embeds,
    normalize_discord_embeds,
    project_discord_embeds,
)


@tag("batch_agent_webhooks")
class DiscordEmbedTests(SimpleTestCase):
    def test_tool_schema_exposes_only_simple_card_fields_and_discord_limits(self):
        schema = discord_embed_tool_schema()

        self.assertEqual(schema["maxItems"], DISCORD_MAX_EMBEDS)
        embed_schema = schema["items"]
        self.assertFalse(embed_schema["additionalProperties"])
        self.assertEqual(
            set(embed_schema["properties"]),
            {"title", "description", "url", "color", "fields"},
        )
        self.assertEqual(embed_schema["properties"]["color"]["pattern"], r"^#[0-9A-Fa-f]{6}$")
        self.assertEqual(embed_schema["properties"]["fields"]["maxItems"], DISCORD_MAX_EMBED_FIELDS)

    def test_normalizes_simple_card_and_hex_color(self):
        self.assertEqual(
            normalize_discord_embeds([{
                "title": " Deployment ",
                "description": " **Healthy** ",
                "url": " https://status.example.test/deployments/42 ",
                "color": "#22c55e",
                "fields": [{"name": " Version ", "value": " v42 ", "inline": True}],
            }]),
            [{
                "title": "Deployment",
                "description": "**Healthy**",
                "url": "https://status.example.test/deployments/42",
                "color": 0x22C55E,
                "fields": [{"name": "Version", "value": "v42", "inline": True}],
            }],
        )

    def test_accepts_documented_count_boundaries(self):
        embeds = [
            {
                "title": str(embed_index),
                "fields": [
                    {"name": str(field_index), "value": "x"}
                    for field_index in range(DISCORD_MAX_EMBED_FIELDS)
                ],
            }
            for embed_index in range(DISCORD_MAX_EMBEDS)
        ]

        self.assertEqual(len(normalize_discord_embeds(embeds)), DISCORD_MAX_EMBEDS)

    def test_rejects_count_limits(self):
        cases = (
            (
                [{"title": "x"}] * (DISCORD_MAX_EMBEDS + 1),
                "at most 10 embeds",
            ),
            (
                [{
                    "fields": [
                        {"name": str(index), "value": "x"}
                        for index in range(DISCORD_MAX_EMBED_FIELDS + 1)
                    ],
                }],
                "at most 25 fields",
            ),
        )
        for embeds, expected in cases:
            with self.subTest(expected=expected), self.assertRaisesRegex(ValueError, expected):
                normalize_discord_embeds(embeds)

    def test_accepts_individual_text_length_boundaries(self):
        cases = (
            [{"title": "x" * DISCORD_EMBED_TITLE_MAX_LENGTH}],
            [{"description": "x" * DISCORD_EMBED_DESCRIPTION_MAX_LENGTH}],
            [{"fields": [{"name": "x" * DISCORD_EMBED_FIELD_NAME_MAX_LENGTH, "value": "x"}]}],
            [{"fields": [{"name": "x", "value": "x" * DISCORD_EMBED_FIELD_VALUE_MAX_LENGTH}]}],
        )
        for embeds in cases:
            with self.subTest(embeds=embeds):
                self.assertTrue(normalize_discord_embeds(embeds))

    def test_rejects_each_text_length_limit(self):
        cases = (
            ([{"title": "x" * (DISCORD_EMBED_TITLE_MAX_LENGTH + 1)}], "title must be 256"),
            ([{"description": "x" * (DISCORD_EMBED_DESCRIPTION_MAX_LENGTH + 1)}], "description must be 4096"),
            (
                [{"fields": [{"name": "x" * (DISCORD_EMBED_FIELD_NAME_MAX_LENGTH + 1), "value": "x"}]}],
                "name must be 256",
            ),
            (
                [{"fields": [{"name": "x", "value": "x" * (DISCORD_EMBED_FIELD_VALUE_MAX_LENGTH + 1)}]}],
                "value must be 1024",
            ),
        )
        for embeds, expected in cases:
            with self.subTest(expected=expected), self.assertRaisesRegex(ValueError, expected):
                normalize_discord_embeds(embeds)

    def test_enforces_combined_text_limit_across_embeds(self):
        exact = [
            {"description": "x" * DISCORD_EMBED_DESCRIPTION_MAX_LENGTH},
            {"description": "x" * (DISCORD_EMBED_TOTAL_TEXT_MAX_LENGTH - DISCORD_EMBED_DESCRIPTION_MAX_LENGTH)},
        ]
        self.assertEqual(len(normalize_discord_embeds(exact)), 2)

        over_limit = [*exact, {"title": "x"}]
        with self.assertRaisesRegex(ValueError, "6000 characters or fewer"):
            normalize_discord_embeds(over_limit)

    def test_rejects_invalid_or_unsupported_card_data(self):
        cases = (
            ([{}], "requires a title, description, or field"),
            ([{"url": "https://example.test"}], "requires a title, description, or field"),
            ([{"title": "x", "type": "rich"}], "unsupported properties: type"),
            ([{"title": "x", "color": "22C55E"}], "color must use #RRGGBB"),
            ([{"title": "x", "url": "javascript:alert(1)"}], r"absolute http\(s\) URL"),
            ([{"fields": "bad"}], "fields must be an array"),
            ([{"title": 42}], "title must be a string"),
            ([{"title": "x", "color": 0x22C55E}], "color must be a string"),
            ([{"fields": [{"name": "x", "value": "y", "url": "bad"}]}], "unsupported properties: url"),
            ([{"fields": [{"name": 42, "value": "y"}]}], "name must be a string"),
            ([{"fields": [{"name": "", "value": "y"}]}], "requires name and value"),
            ([{"fields": [{"name": "x", "value": "y", "inline": "true"}]}], "inline must be boolean"),
        )
        for embeds, expected in cases:
            with self.subTest(expected=expected), self.assertRaisesRegex(ValueError, expected):
                normalize_discord_embeds(embeds)

    def test_rejects_raw_html_in_all_textual_card_fields(self):
        cases = (
            [{"title": "<strong>Title</strong>"}],
            [{"description": "<div>Description</div>"}],
            [{"fields": [{"name": "<b>Name</b>", "value": "Value"}]}],
            [{"fields": [{"name": "Name", "value": "<span>Value</span>"}]}],
        )
        for embeds in cases:
            with self.subTest(embeds=embeds), self.assertRaisesRegex(ValueError, "Markdown, not raw HTML"):
                normalize_discord_embeds(embeds)

    def test_formats_received_read_only_metadata_for_agents(self):
        rendered = format_discord_embeds([{
            "title": "Deployment",
            "description": "Healthy",
            "color": 0x22C55E,
            "author": {
                "name": "Release Bot",
                "url": "https://example.test/author",
                "icon_url": "https://example.test/author.png",
            },
            "provider": {"name": "Status", "url": "https://example.test"},
            "fields": [{"name": "Version", "value": "v42", "inline": True}],
            "footer": {"text": "Updated", "icon_url": "https://example.test/footer.png"},
            "image": {"url": "https://example.test/image.png"},
            "thumbnail": {"url": "https://example.test/thumb.png"},
            "video": {"url": "https://example.test/video.mp4"},
        }])

        self.assertEqual(json.loads(rendered), project_discord_embeds([{
            "title": "Deployment",
            "description": "Healthy",
            "color": 0x22C55E,
            "author": {
                "name": "Release Bot",
                "url": "https://example.test/author",
                "icon_url": "https://example.test/author.png",
            },
            "provider": {"name": "Status", "url": "https://example.test"},
            "fields": [{"name": "Version", "value": "v42", "inline": True}],
            "footer": {"text": "Updated", "icon_url": "https://example.test/footer.png"},
            "image": {"url": "https://example.test/image.png"},
            "thumbnail": {"url": "https://example.test/thumb.png"},
            "video": {"url": "https://example.test/video.mp4"},
        }]))

    def test_projects_received_metadata_for_live_chat(self):
        projected = project_discord_embeds([{
            "title": "Deployment",
            "description": "Healthy",
            "url": "https://example.test/deploy",
            "color": 0x22C55E,
            "author": {
                "name": "Release Bot",
                "url": "https://example.test/author",
                "icon_url": "https://example.test/author.png",
            },
            "provider": {"name": "Status", "url": "https://example.test"},
            "fields": [{"name": "Version", "value": "v42", "inline": True}],
            "footer": {"text": "Updated", "icon_url": "https://example.test/footer.png"},
            "image": {"url": "https://example.test/image.png"},
            "thumbnail": {"url": "https://example.test/thumb.png"},
            "video": {"url": "https://example.test/video.mp4"},
        }])

        self.assertEqual(projected, [{
            "title": "Deployment",
            "description": "Healthy",
            "url": "https://example.test/deploy",
            "color": "#22C55E",
            "author": {
                "name": "Release Bot",
                "url": "https://example.test/author",
                "iconUrl": "https://example.test/author.png",
            },
            "footer": {
                "text": "Updated",
                "iconUrl": "https://example.test/footer.png",
            },
            "provider": {"name": "Status", "url": "https://example.test"},
            "fields": [{"name": "Version", "value": "v42", "inline": True}],
            "imageUrl": "https://example.test/image.png",
            "thumbnailUrl": "https://example.test/thumb.png",
            "videoUrl": "https://example.test/video.mp4",
        }])

    def test_live_chat_projection_drops_unsafe_urls(self):
        projected = project_discord_embeds([{
            "title": "Safe text",
            "url": "javascript:alert(1)",
            "image": {"url": "data:text/html,bad"},
            "author": {"name": "Author", "url": "file:///tmp/bad"},
        }])

        self.assertEqual(projected, [{"title": "Safe text", "author": {"name": "Author"}}])

    def test_signature_projection_ignores_discord_read_only_fields(self):
        outbound = [{"title": "Deployment", "fields": [{"name": "Version", "value": "v42"}]}]
        gateway = [{
            "type": "rich",
            "title": "Deployment",
            "provider": {"name": "Discord"},
            "fields": [{"name": "Version", "value": "v42", "inline": False}],
        }]

        self.assertEqual(
            discord_embed_signature_projection(outbound),
            discord_embed_signature_projection(gateway),
        )
        self.assertEqual(
            discord_embed_signature_projection([{"type": "article", "title": "Automatic link preview"}]),
            [],
        )
