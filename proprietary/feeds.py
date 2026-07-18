from django.conf import settings
from django.contrib.syndication.views import Feed, FeedDoesNotExist
from django.urls import reverse

from proprietary.utils_blog import get_all_blog_posts


BLOG_FEED_ITEM_LIMIT = 20


class BlogFeed(Feed):
    title = "Gobii AI Agent Automation Blog"
    description = (
        "Guides, release notes, and engineering lessons for teams building with "
        "always-on AI agents, browser automation, MCP integrations, and production "
        "safety controls."
    )
    language = "en-US"
    item_guid_is_permalink = True

    def get_object(self, request, *args, **kwargs):
        if not settings.GOBII_PROPRIETARY_MODE:
            raise FeedDoesNotExist
        return None

    def link(self):
        return self._absolute_url(reverse("proprietary:blog_index"))

    def feed_url(self):
        return self._absolute_url(reverse("proprietary:blog_feed"))

    def items(self):
        posts = sorted(
            get_all_blog_posts(),
            key=lambda post: post["updated_at"] or post["published_at"],
            reverse=True,
        )
        return posts[:BLOG_FEED_ITEM_LIMIT]

    def item_title(self, item):
        return item["title"]

    def item_description(self, item):
        return item["summary"]

    def item_link(self, item):
        return self._absolute_url(item["url"])

    def item_guid(self, item):
        return self.item_link(item)

    def item_pubdate(self, item):
        return item["published_at"]

    def item_updateddate(self, item):
        return item["updated_at"] or item["published_at"]

    def item_author_name(self, item):
        return item["meta"]["author"]

    def item_author_link(self, item):
        author_url = item["meta"].get("author_url")
        return self._absolute_url(author_url) if author_url else None

    def item_categories(self, item):
        categories = item["meta"].get("tags") or item["meta"].get("keywords") or ()
        if isinstance(categories, str):
            return [value.strip() for value in categories.split(",") if value.strip()]
        return [str(value) for value in categories if value]

    @staticmethod
    def _absolute_url(path):
        if path.startswith(("http://", "https://")):
            return path
        return f"{settings.PUBLIC_SITE_URL.rstrip('/')}/{path.lstrip('/')}"
